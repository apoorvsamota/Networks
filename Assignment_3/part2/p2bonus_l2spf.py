# p2bonus_l2spf.py - FINAL, DEFINITIVELY CORRECT, DYNAMIC & BONUS VERSION

import json
import time
import networkx as nx
from operator import attrgetter

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub
from ryu.topology import event
from ryu.topology.switches import Switches

class WeightedLoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'switches': Switches}

    def __init__(self, *args, **kwargs):
        super(WeightedLoadBalancer, self).__init__(*args, **kwargs)
        self.net = nx.DiGraph()
        self.datapaths = {}
        self.host_location = {}
        self.link_load = {}
        self.port_stats = {}
        self.monitor_interval = 5
        self.monitor_thread = hub.spawn(self._monitor)
        with open('config.json') as f:
            self.config = json.load(f)
        self.logger.info("--- Bonus Controller Initialized ---")

    @set_ev_cls(event.EventSwitchEnter)
    def handler_switch_enter(self, ev):
        self.net.add_node(ev.switch.dp.id)

    @set_ev_cls(event.EventLinkAdd)
    def handler_link_add(self, ev):
        link = ev.link
        src, dst = link.src, link.dst
        weight = self.get_link_weight(src.dpid, dst.dpid)
        self.net.add_edge(src.dpid, dst.dpid, port=src.port_no, weight=weight)
        self.net.add_edge(dst.dpid, src.dpid, port=dst.port_no, weight=weight)
        self.link_load.setdefault((src.dpid, dst.dpid), 0)
        self.link_load.setdefault((dst.dpid, src.dpid), 0)
        self.logger.info("Link discovered: s%d(p%d) <-> s%d(p%d)", src.dpid, src.port_no, dst.dpid, dst.port_no)

    def get_link_weight(self, dpid1, dpid2):
        nodes, matrix = self.config['nodes'], self.config['weight_matrix']
        try:
            idx1, idx2 = nodes.index(f's{dpid1}'), nodes.index(f's{dpid2}')
            return matrix[idx1][idx2]
        except (ValueError, IndexError): return 1

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_port_stats(dp)
            hub.sleep(self.monitor_interval)

    def _request_port_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in sorted(ev.msg.body, key=attrgetter('port_no')):
            for u, v, data in self.net.edges(dpid, data=True):
                if data.get('port') == stat.port_no:
                    key = (u, v)
                    tx_bytes = stat.tx_bytes
                    if key in self.port_stats:
                       prev_bytes, prev_time = self.port_stats[key]
                       if time.time() > prev_time:
                           rate_bps = (tx_bytes - prev_bytes) * 8 / (time.time() - prev_time)
                           self.link_load[key] = rate_bps
                           self.logger.info("Load on link (%d -> %d): %.2f Mbps", u, v, rate_bps / 1e6)
                    self.port_stats[key] = (tx_bytes, time.time())
                    break

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        self.add_flow(datapath, 0, datapath.ofproto_parser.OFPMatch(),
                      [datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER, datapath.ofproto.OFPCML_NO_BUFFER)])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: return

        src, dst = eth.src, eth.dst
        src_dpid = datapath.id

        if src not in self.host_location:
            self.host_location[src] = (src_dpid, in_port)

        if dst in self.host_location:
            dst_dpid, dst_port = self.host_location[dst]
            
            if not (self.net.has_node(src_dpid) and self.net.has_node(dst_dpid)):
                self._flood(datapath, msg)
                return

            try:
                paths = list(nx.all_shortest_paths(self.net, source=src_dpid, target=dst_dpid, weight='weight'))
            except nx.NetworkXNoPath:
                self._flood(datapath, msg)
                return

            best_path = None
            if len(paths) > 1:
                min_bottleneck_load = float('inf')
                for path in paths:
                    bottleneck_load = 0
                    for i in range(len(path) - 1):
                        u, v = path[i], path[i+1]
                        load = self.link_load.get((u, v), 0)
                        bottleneck_load = max(bottleneck_load, load)
                    if bottleneck_load < min_bottleneck_load:
                        min_bottleneck_load = bottleneck_load
                        best_path = path
                self.logger.info("--- Chose path %s based on load ---", best_path)
            else:
                best_path = paths[0]
            
            path_to_install = best_path
            for i in range(len(path_to_install) - 1):
                u, v = path_to_install[i], path_to_install[i+1]
                self.add_flow(self.datapaths[u], 1, self.datapaths[u].ofproto_parser.OFPMatch(eth_dst=dst), [self.datapaths[u].ofproto_parser.OFPActionOutput(self.net[u][v]['port'])])
                self.add_flow(self.datapaths[v], 1, self.datapaths[v].ofproto_parser.OFPMatch(eth_dst=src), [self.datapaths[v].ofproto_parser.OFPActionOutput(self.net[v][u]['port'])])

            self.add_flow(self.datapaths[dst_dpid], 1, self.datapaths[dst_dpid].ofproto_parser.OFPMatch(eth_dst=dst), [self.datapaths[dst_dpid].ofproto_parser.OFPActionOutput(dst_port)])
            self.add_flow(self.datapaths[src_dpid], 1, self.datapaths[src_dpid].ofproto_parser.OFPMatch(eth_dst=src), [self.datapaths[src_dpid].ofproto_parser.OFPActionOutput(in_port)])

            first_hop_port = self.net[src_dpid][path_to_install[1]]['port'] if len(path_to_install) > 1 else dst_port
            out = datapath.ofproto_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=[datapath.ofproto_parser.OFPActionOutput(first_hop_port)], data=msg.data)
            datapath.send_msg(out)
        else:
            self._flood(datapath, msg)

    def _flood(self, datapath, msg):
        parser = datapath.ofproto_parser
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=msg.match['in_port'],
                                  actions=[parser.OFPActionOutput(datapath.ofproto.OFPP_FLOOD)], data=msg.data)
        datapath.send_msg(out)

    def add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                idle_timeout=0, hard_timeout=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)
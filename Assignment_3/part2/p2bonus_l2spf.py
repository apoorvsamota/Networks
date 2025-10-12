# p2bonus_l2spf.py - FINAL, DEFINITIVELY CORRECTED VERSION

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

class WeightedLoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(WeightedLoadBalancer, self).__init__(*args, **kwargs)
        self.net = nx.Graph()
        self.datapaths = {}
        self.host_location = {}
        self.port_stats = {}
        self.link_load = {}
        self.monitor_interval = 5
        self.monitor_thread = hub.spawn(self._monitor)

        with open('config.json') as config_file:
            config = json.load(config_file)
            nodes = config['nodes']
            matrix = config['weight_matrix']
            for i, node_name in enumerate(nodes):
                self.net.add_node(int(node_name[1:]))
            for i in range(len(matrix)):
                for j in range(i, len(matrix[i])):
                    if matrix[i][j] > 0:
                        u, v = int(nodes[i][1:]), int(nodes[j][1:])
                        self.net.add_edge(u, v, weight=matrix[i][j])
                        self.link_load[(u, v)] = 0
                        self.link_load[(v, u)] = 0
        self.logger.info("--- Bonus Controller Initialized ---")

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_port_stats(dp)
            hub.sleep(self.monitor_interval)

    def _request_port_stats(self, datapath):
        ofp_parser = datapath.ofproto_parser
        req = ofp_parser.OFPPortStatsRequest(datapath, 0, datapath.ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        for stat in sorted(body, key=attrgetter('port_no')):
            port_no = stat.port_no
            if port_no != ev.msg.datapath.ofproto.OFPP_LOCAL:
                key = (dpid, port_no)
                tx_bytes = stat.tx_bytes
                if key in self.port_stats:
                    prev_bytes, prev_time = self.port_stats[key]
                    time_diff = time.time() - prev_time
                    if time_diff > 0:
                        rate_bps = (tx_bytes - prev_bytes) * 8 / time_diff
                        for u, v in self.net.edges(dpid):
                            if self._get_port_for_link(u, v) == port_no:
                                self.link_load[(u, v)] = rate_bps
                                self.logger.info("Load on link (%d -> %d): %.2f Mbps", u, v, rate_bps / 1e6)
                                break
                self.port_stats[key] = (tx_bytes, time.time())

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        self.add_flow(datapath, 0, datapath.ofproto_parser.OFPMatch(),
                      [datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER,
                                                              datapath.ofproto.OFPCML_NO_BUFFER)])
        self.logger.info("Switch %s connected", datapath.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP or eth.dst.startswith('01:80:c2'):
            return

        src, dst = eth.src, eth.dst
        if src not in self.host_location:
            self.host_location[src] = (dpid, in_port)

        if dst in self.host_location:
            src_dpid, _ = self.host_location[src]
            dst_dpid, dst_port = self.host_location[dst]
            
            paths = list(nx.all_shortest_paths(self.net, source=src_dpid, target=dst_dpid, weight='weight'))
            if not paths: return

            best_path = None
            if len(paths) > 1:
                min_bottleneck_load = float('inf')
                self.logger.info("--- Evaluating %d Equal Cost Paths for flow %s -> %s ---", len(paths), src, dst)
                for path in paths:
                    bottleneck_load = 0
                    for i in range(len(path) - 1):
                        u, v = path[i], path[i+1]
                        load = self.link_load.get((u, v), 0)
                        bottleneck_load = max(bottleneck_load, load)
                    self.logger.info("Path %s has bottleneck load: %.2f Mbps", path, bottleneck_load / 1e6)
                    if bottleneck_load < min_bottleneck_load:
                        min_bottleneck_load = bottleneck_load
                        best_path = path
                self.logger.info("--- Chose path %s ---", best_path)
            else:
                best_path = paths[0]

            path_to_install = best_path
            
            for i in range(len(path_to_install) - 1):
                u, v = path_to_install[i], path_to_install[i+1]
                out_port = self._get_port_for_link(u, v)
                if out_port:
                    self.add_flow(self.datapaths[u], 1, datapath.ofproto_parser.OFPMatch(eth_dst=dst),
                                  [datapath.ofproto_parser.OFPActionOutput(out_port)])
                out_port_rev = self._get_port_for_link(v, u)
                if out_port_rev:
                    self.add_flow(self.datapaths[v], 1, datapath.ofproto_parser.OFPMatch(eth_dst=src),
                                  [datapath.ofproto_parser.OFPActionOutput(out_port_rev)])

            self.add_flow(self.datapaths[dst_dpid], 1, datapath.ofproto_parser.OFPMatch(eth_dst=dst),
                          [datapath.ofproto_parser.OFPActionOutput(dst_port)])
            self.add_flow(self.datapaths[src_dpid], 1, datapath.ofproto_parser.OFPMatch(eth_dst=src),
                          [datapath.ofproto_parser.OFPActionOutput(self.host_location[src][1])])

            first_hop_port = self._get_port_for_link(src_dpid, path_to_install[1]) if len(path_to_install) > 1 else dst_port
            actions = [datapath.ofproto_parser.OFPActionOutput(first_hop_port)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                actions=actions, data=msg.data if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER else None)
            datapath.send_msg(out)
        else:
            actions = [datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_FLOOD)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def _get_port_for_link(self, dpid1, dpid2):
        """
        Calculates the correct port map based on the 4-host p2_topo.py
        """
        port_map = {
            # s1: h1=1, h3=2, s2=3, s3=4
            (1, 2): 3, (2, 1): 1,
            (1, 3): 4, (3, 1): 1,
            # s2: s1=1, s4=2
            (2, 4): 2, (4, 2): 1,
            # s3: s1=1, s5=2
            (3, 5): 2, (5, 3): 1,
            # s4: s2=1, s6=3
            # s6: h2=1, h4=2, s4=3, s5=4
            (4, 6): 2, (6, 4): 3,
            # s5: s3=1, s6=2
            (5, 6): 2, (6, 5): 4
        }
        return port_map.get((dpid1, dpid2))
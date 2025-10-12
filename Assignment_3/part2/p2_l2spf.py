# p2_l2spf.py - FINAL, DEFINITIVELY CORRECT, EVENT-DRIVEN VERSION

import json
import random
import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.topology import event
from ryu.topology.switches import Switches

class ShortestPathController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'switches': Switches}

    def __init__(self, *args, **kwargs):
        super(ShortestPathController, self).__init__(*args, **kwargs)
        self.net = nx.DiGraph()
        self.datapaths = {}
        self.host_location = {}
        with open('config.json') as f:
            self.config = json.load(f)
        self.logger.info("--- L2 Shortest Path Controller Initialized ---")

    @set_ev_cls(event.EventSwitchEnter)
    def handler_switch_enter(self, ev):
        self.net.add_node(ev.switch.dp.id)
        self.logger.info("Switch %d connected.", ev.switch.dp.id)

    @set_ev_cls(event.EventLinkAdd)
    def handler_link_add(self, ev):
        link = ev.link
        src, dst = link.src, link.dst
        weight = self.get_link_weight(src.dpid, dst.dpid)
        self.net.add_edge(src.dpid, dst.dpid, port=src.port_no, weight=weight)
        self.net.add_edge(dst.dpid, src.dpid, port=dst.port_no, weight=weight)
        self.logger.info("Link discovered: s%d(p%d) <-> s%d(p%d)", src.dpid, src.port_no, dst.dpid, dst.port_no)

    def get_link_weight(self, dpid1, dpid2):
        nodes, matrix = self.config['nodes'], self.config['weight_matrix']
        try:
            idx1, idx2 = nodes.index(f's{dpid1}'), nodes.index(f's{dpid2}')
            return matrix[idx1][idx2]
        except (ValueError, IndexError): return 1

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
            
            path = random.choice(paths) if self.config.get('ecmp', False) and len(paths) > 1 else paths[0]
            self.logger.info("Path chosen for %s -> %s: %s", src, dst, path)

            # Install proactive forward and reverse rules
            for i in range(len(path) - 1):
                u, v = path[i], path[i+1]
                self.add_flow(self.datapaths[u], 1, self.datapaths[u].ofproto_parser.OFPMatch(eth_dst=dst), [self.datapaths[u].ofproto_parser.OFPActionOutput(self.net[u][v]['port'])])
                self.add_flow(self.datapaths[v], 1, self.datapaths[v].ofproto_parser.OFPMatch(eth_dst=src), [self.datapaths[v].ofproto_parser.OFPActionOutput(self.net[v][u]['port'])])

            self.add_flow(self.datapaths[dst_dpid], 1, self.datapaths[dst_dpid].ofproto_parser.OFPMatch(eth_dst=dst), [self.datapaths[dst_dpid].ofproto_parser.OFPActionOutput(dst_port)])
            self.add_flow(self.datapaths[src_dpid], 1, self.datapaths[src_dpid].ofproto_parser.OFPMatch(eth_dst=src), [self.datapaths[src_dpid].ofproto_parser.OFPActionOutput(in_port)])

            first_hop_port = self.net[src_dpid][path[1]]['port'] if len(path) > 1 else dst_port
            out = datapath.ofproto_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, 
                                                       actions=[datapath.ofproto_parser.OFPActionOutput(first_hop_port)], data=msg.data)
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
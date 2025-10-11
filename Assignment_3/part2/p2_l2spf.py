# p2_l2spf.py - FINAL CORRECTED VERSION

import json
import random
import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types

class ShortestPathController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ShortestPathController, self).__init__(*args, **kwargs)
        self.net = nx.Graph()
        self.datapaths = {}
        self.host_location = {} # {mac: (dpid, port)}

        with open('config.json') as config_file:
            config = json.load(config_file)
            self.ecmp = config.get('ecmp', False)
            nodes = config['nodes']
            matrix = config['weight_matrix']
            
            for i, node_name in enumerate(nodes):
                self.net.add_node(int(node_name[1:]))

            for i in range(len(matrix)):
                for j in range(i, len(matrix[i])):
                    if matrix[i][j] > 0:
                        node1 = int(nodes[i][1:])
                        node2 = int(nodes[j][1:])
                        self.net.add_edge(node1, node2, weight=matrix[i][j])
        
        self.logger.info("--- Network Graph Loaded ---")
        self.logger.info("ECMP enabled: %s", self.ecmp)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER,
                                          datapath.ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch %s connected", datapath.id)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    def _get_port_for_link(self, dpid1, dpid2):
        # Corrected port map based on p2_topo.py
        # s1: h1=1, s2=2, s3=3
        # s2: s1=1, s4=2
        # s3: s1=1, s5=2
        # s4: s2=1, s6=2
        # s5: s3=1, s6=2
        # s6: h2=1, s4=2, s5=3
        port_map = {
            (1, 2): 2, (2, 1): 1,
            (1, 3): 3, (3, 1): 1,
            (2, 4): 2, (4, 2): 1,
            (3, 5): 2, (5, 3): 1,
            (4, 6): 2, (6, 4): 2, # Corrected
            (5, 6): 2, (6, 5): 3  # Corrected
        }
        return port_map.get((dpid1, dpid2))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        
        # Ignore non-user traffic (LLDP, STP)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP or eth.dst.startswith('01:80:c2'):
            return

        src = eth.src
        dst = eth.dst

        # Learn host location
        if src not in self.host_location:
            self.host_location[src] = (dpid, in_port)
            self.logger.info("Learned host %s at switch %d port %d", src, dpid, in_port)

        # If destination is known, find path and install flows
        if dst in self.host_location:
            src_dpid, _ = self.host_location[src]
            dst_dpid, dst_port = self.host_location[dst]

            paths = list(nx.all_shortest_paths(self.net, source=src_dpid, target=dst_dpid, weight='weight'))
            
            if not paths: return

            path = random.choice(paths) if self.ecmp and len(paths) > 1 else paths[0]
            self.logger.info("Path chosen from %d to %d: %s", src_dpid, dst_dpid, path)

            # Install flows for the chosen path (both directions)
            for i in range(len(path) - 1):
                # Forward path
                u, v = path[i], path[i+1]
                out_port = self._get_port_for_link(u, v)
                if out_port:
                    match = datapath.ofproto_parser.OFPMatch(eth_dst=dst)
                    actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]
                    self.add_flow(self.datapaths[u], 1, match, actions)

                # Reverse path
                out_port_rev = self._get_port_for_link(v, u)
                if out_port_rev:
                    match_rev = datapath.ofproto_parser.OFPMatch(eth_dst=src)
                    actions_rev = [datapath.ofproto_parser.OFPActionOutput(out_port_rev)]
                    self.add_flow(self.datapaths[v], 1, match_rev, actions_rev)

            # Install final hop rules to hosts
            self.add_flow(self.datapaths[dst_dpid], 1, 
                          datapath.ofproto_parser.OFPMatch(eth_dst=dst), 
                          [datapath.ofproto_parser.OFPActionOutput(dst_port)])
            
            self.add_flow(self.datapaths[src_dpid], 1, 
                          datapath.ofproto_parser.OFPMatch(eth_dst=src), 
                          [datapath.ofproto_parser.OFPActionOutput(self.host_location[src][1])])

            # Forward the packet that triggered the installation
            first_hop_port = self._get_port_for_link(src_dpid, path[1]) if len(path) > 1 else dst_port
            actions = [datapath.ofproto_parser.OFPActionOutput(first_hop_port)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                actions=actions, data=msg.data if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER else None)
            datapath.send_msg(out)

        else: # If destination is unknown, flood (for ARP)
            actions = [datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_FLOOD)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                actions=actions, data=msg.data)
            datapath.send_msg(out)
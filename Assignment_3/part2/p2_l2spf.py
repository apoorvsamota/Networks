# p2_l2spf.py

import json
import random
import networkx as nx

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib.packet import arp

class ShortestPathController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ShortestPathController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.net = nx.Graph()
        self.datapaths = {} # Maps DPID to datapath object
        
        # This will store the location of hosts: {mac: (dpid, port)}
        self.host_location = {}

        # --- Load configuration and build the graph ---
        with open('config.json') as config_file:
            config = json.load(config_file)
            self.ecmp = config.get('ecmp', False)
            nodes = config['nodes']
            matrix = config['weight_matrix']
            
            # Add nodes (switches) to the graph
            for i, node_name in enumerate(nodes):
                # We use the switch name as the node identifier in networkx
                # We map it to its datapath ID (dpid), which we assume is its number
                self.net.add_node(int(node_name[1:]))

            # Add weighted edges (links) to the graph
            for i in range(len(matrix)):
                for j in range(i, len(matrix[i])):
                    if matrix[i][j] > 0:
                        # The nodes in our graph are the switch numbers (1, 2, 3...)
                        node1 = int(nodes[i][1:])
                        node2 = int(nodes[j][1:])
                        self.net.add_edge(node1, node2, weight=matrix[i][j])
        
        self.logger.info("--- Network Graph ---")
        self.logger.info("Nodes: %s", self.net.nodes())
        self.logger.info("Edges: %s", self.net.edges(data=True))
        self.logger.info("ECMP enabled: %s", self.ecmp)


    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Store the datapath object for later use
        self.datapaths[datapath.id] = datapath

        # Install the default table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        self.logger.info("Switch %s connected", datapath.id)

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                match=match, instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # --- FIX 1: Ignore LLDP and STP packets ---
        if eth.ethertype == ether_types.ETH_TYPE_LLDP or eth.dst.startswith('01:80:c2'):
            return
            
        dst = eth.dst
        src = eth.src

        # Learn the location of the source host
        if src not in self.host_location:
            self.host_location[src] = (dpid, in_port)
            self.logger.info("Learned host %s is at switch %s on port %s", src, dpid, in_port)

        # If we know the destination, calculate path and install flows
        if dst in self.host_location:
            src_dpid, _ = self.host_location[src]
            dst_dpid, dst_port = self.host_location[dst]
            
            path_to_install = []
            
            # Calculate the shortest path(s)
            paths = list(nx.all_shortest_paths(self.net, source=src_dpid, target=dst_dpid, weight='weight'))
            
            if not paths:
                self.logger.error("No path found from %s to %s", src_dpid, dst_dpid)
                return

            if self.ecmp and len(paths) > 1:
                path_to_install = random.choice(paths)
                self.logger.info("ECMP: Multiple paths found. Randomly chose: %s", path_to_install)
            else:
                path_to_install = paths[0]
                self.logger.info("Shortest path found: %s", path_to_install)

            # --- FIX 2: Install rules for BOTH forward and reverse paths ---
            # Install flows along the path from source to destination
            for i in range(len(path_to_install) - 1):
                # Forward path (src -> dst)
                current_dpid = path_to_install[i]
                next_dpid = path_to_install[i+1]
                out_port = self._get_port_for_link(current_dpid, next_dpid)
                if out_port:
                    dp = self.datapaths[current_dpid]
                    match = parser.OFPMatch(eth_dst=dst)
                    actions = [parser.OFPActionOutput(out_port)]
                    self.add_flow(dp, 1, match, actions)

                # Reverse path (dst -> src)
                # The next switch in the forward path is the current switch in the reverse path
                current_dpid_rev = path_to_install[i+1]
                prev_dpid_rev = path_to_install[i]
                out_port_rev = self._get_port_for_link(current_dpid_rev, prev_dpid_rev)
                if out_port_rev:
                    dp_rev = self.datapaths[current_dpid_rev]
                    match_rev = parser.OFPMatch(eth_dst=src)
                    actions_rev = [parser.OFPActionOutput(out_port_rev)]
                    self.add_flow(dp_rev, 1, match_rev, actions_rev)

            # Install the final hop rule on the destination switch
            final_dp = self.datapaths[dst_dpid]
            match = parser.OFPMatch(eth_dst=dst)
            actions = [parser.OFPActionOutput(dst_port)]
            self.add_flow(final_dp, 1, match, actions)

            # Install the first hop rule for the reverse path
            first_dp_rev = self.datapaths[src_dpid]
            src_port, _ = self.host_location[src]
            match_rev = parser.OFPMatch(eth_dst=src)
            actions_rev = [parser.OFPActionOutput(in_port)] # in_port is where the host is connected
            self.add_flow(first_dp_rev, 1, match_rev, actions_rev)

            # --- Now, forward the original packet that triggered this ---
            out_port = self._get_port_for_link(src_dpid, path_to_install[1]) if len(path_to_install) > 1 else dst_port
            actions = [parser.OFPActionOutput(out_port)]
            data = None
            if msg.buffer_id == ofproto.OFP_NO_BUFFER:
                data = msg.data
            
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=data)
            datapath.send_msg(out)

        else:
            # If destination is unknown, flood the packet
            actions = [parser.OFPActionOutput(ofproto.OFPP_FLOOD)]
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                    in_port=in_port, actions=actions, data=msg.data)
            datapath.send_msg(out)

    def _get_port_for_link(self, dpid1, dpid2):
        """
        Calculates the correct port map based on the 2-host p2_topo.py
        s1 links: h1=1, s2=2, s3=3
        s2 links: s1=1, s4=2
        s3 links: s1=1, s5=2
        s4 links: s2=1, s6=2
        s5 links: s3=1, s6=2
        s6 links: h2=1, s4=2, s5=3  <-- THIS IS THE KEY
        """
        port_map = {
            (1, 2): 2, (2, 1): 1,
            (1, 3): 3, (3, 1): 1,
            (2, 4): 2, (4, 2): 1,
            (3, 5): 2, (5, 3): 1,
            (4, 6): 2, (6, 4): 2,  # Corrected
            (5, 6): 2, (6, 5): 3   # Corrected
        }
        return port_map.get((dpid1, dpid2))
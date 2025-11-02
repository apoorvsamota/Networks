# p3_l3spf.py

import json
import networkx as nx
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4

class L3RouterController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(L3RouterController, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.net = nx.Graph()

        # Load the entire network configuration from the JSON file
        with open('p3_config.json') as f:
            self.config = json.load(f)

        # Pre-populate ARP table and other mappings from the config
        self.arp_table = {}  # {ip: mac}
        self.switch_interfaces = {} # {dpid: {ip: mac}}
        for host in self.config['hosts']:
            self.arp_table[host['ip']] = host['mac']
        for switch in self.config['switches']:
            dpid = switch['dpid']
            self.switch_interfaces[dpid] = {}
            for iface in switch['interfaces']:
                self.arp_table[iface['ip']] = iface['mac']
                self.switch_interfaces[dpid][iface['ip']] = iface['mac']

        # Build the graph for Dijkstra's algorithm
        for link in self.config['links']:
            u, v = int(link['src'][1:]), int(link['dst'][1:])
            self.net.add_edge(u, v, weight=link['cost'])
        
        self.logger.info("--- L3 Router Controller Initialized with Static Config ---")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        parser = datapath.ofproto_parser
        #--------------------
        # in switch_features_handler after obtaining parser and ofproto
        # match LLDP (ethertype 0x88cc)
        ofproto = datapath.ofproto
        lldp_match = parser.OFPMatch(eth_type=0x88cc)
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        # use very high priority so it is matched before other rules
        mod = parser.OFPFlowMod(datapath=datapath, priority=65535, match=lldp_match,
                                instructions=[parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)])
        datapath.send_msg(mod)


        #---------------
        self.add_flow(datapath, 0, parser.OFPMatch(),
                      [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER, datapath.ofproto.OFPCML_NO_BUFFER)])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self._handle_arp(msg, pkt)
        elif eth.ethertype == ether_types.ETH_TYPE_IP:
            self._handle_ip(msg, pkt)

    def _handle_arp(self, msg, pkt):
        datapath, in_port = msg.datapath, msg.match['in_port']
        arp_pkt = pkt.get_protocol(arp.arp)

        # Check if this is an ARP request for a gateway IP we know
        if arp_pkt.dst_ip in self.arp_table:
            self.logger.info("ARP Request for %s. Replying.", arp_pkt.dst_ip)
            
            src_mac = self.arp_table[arp_pkt.dst_ip] # MAC of the gateway interface
            dst_mac = arp_pkt.src_mac
            
            # Construct ARP Reply
            reply = packet.Packet()
            reply.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP, dst=dst_mac, src=src_mac))
            reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=src_mac, src_ip=arp_pkt.dst_ip,
                                       dst_mac=dst_mac, dst_ip=arp_pkt.src_ip))
            reply.serialize()
            
            # Send the reply back out the port it came from
            actions = [datapath.ofproto_parser.OFPActionOutput(in_port)]
            out = datapath.ofproto_parser.OFPPacketOut(datapath=datapath, buffer_id=datapath.ofproto.OFP_NO_BUFFER,
                                                      in_port=datapath.ofproto.OFPP_CONTROLLER,
                                                      actions=actions, data=reply.data)
            datapath.send_msg(out)

    def _handle_ip(self, msg, pkt):
        datapath = msg.datapath
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        src_dpid = datapath.id
        dst_host_info = next((h for h in self.config['hosts'] if h['ip'] == ip_pkt.dst), None)
        if not dst_host_info: return
        dst_dpid = int(dst_host_info['switch'][1:])

        # Calculate the shortest path between the source and destination switches
        path = nx.shortest_path(self.net, source=src_dpid, target=dst_dpid, weight='weight')
        self.logger.info("IP Path for %s -> %s: %s", ip_pkt.src, ip_pkt.dst, path)

        # Install flow rules for each hop in the path
        for i in range(len(path) - 1):
            u_dpid, v_dpid = path[i], path[i+1]
            # Get interface info for this hop
            u_iface = self._get_interface_info(u_dpid, neighbor=f's{v_dpid}')
            v_iface = self._get_interface_info(v_dpid, neighbor=f's{u_dpid}')

            if u_iface and v_iface:
                out_port = int(u_iface['name'].split('-eth')[1])
                dp = self.datapaths[u_dpid]
                parser = dp.ofproto_parser
                actions = [
                    parser.OFPActionDecNwTtl(),
                    parser.OFPActionSetField(eth_src=u_iface['mac']),
                    parser.OFPActionSetField(eth_dst=v_iface['mac']),
                    parser.OFPActionOutput(out_port)
                ]
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=ip_pkt.dst)
                self.add_flow(dp, 1, match, actions)
        
        # Install the final hop rule (from the last router to the destination host)
        last_router_dpid = path[-1]
        last_router_iface = self._get_interface_info(last_router_dpid, neighbor=dst_host_info['name'])
        
        if last_router_iface:
            out_port = int(last_router_iface['name'].split('-eth')[1])
            dp = self.datapaths[last_router_dpid]
            parser = dp.ofproto_parser
            actions = [
                parser.OFPActionDecNwTtl(),
                parser.OFPActionSetField(eth_src=last_router_iface['mac']),
                parser.OFPActionSetField(eth_dst=dst_host_info['mac']),
                parser.OFPActionOutput(out_port)
            ]
            match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=dst_host_info['ip'])
            self.add_flow(dp, 1, match, actions)

    def _get_interface_info(self, dpid, neighbor):
        switch = next((s for s in self.config['switches'] if s['dpid'] == dpid), None)
        if switch:
            return next((iface for iface in switch['interfaces'] if iface['neighbor'] == neighbor), None)
        return None

    def add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                idle_timeout=0, hard_timeout=0,
                                match=match, instructions=inst)
        datapath.send_msg(mod)
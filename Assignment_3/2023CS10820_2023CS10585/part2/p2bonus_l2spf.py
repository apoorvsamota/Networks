# p2bonus_l2spf.py - Dynamic Per-Flow Load Balancer

import json
import time
import random
import networkx as nx
from operator import attrgetter

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, tcp, udp
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
        self.host_location = {} # MAC -> (dpid, port)
        self.link_load = {}     # (u, v) -> bps
        self.port_stats = {}    # (u, v) -> (prev_bytes, prev_time)
        self.monitor_interval = 2 # Shorter interval for faster reaction
        
        # Store installed flows per (src_ip, dst_ip, proto, src_port, dst_port) tuple
        self.installed_flows = set() 
        
        self.monitor_thread = hub.spawn(self._monitor)
        with open('config.json') as f:
            self.config = json.load(f)
        self.logger.info("--- Dynamic Per-Flow Load Balancer Initialized ---")

    # --- Topology Discovery (Unchanged) ---
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

    # --- Load Monitoring (Unchanged) ---
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
                       now = time.time()
                       if now > prev_time:
                           rate_bps = max(0, (tx_bytes - prev_bytes) * 8 / (now - prev_time))
                           self.link_load[key] = rate_bps
                           if rate_bps > 1000: # Only log significant load
                               self.logger.info("Load on link (%d -> %d): %.2f Mbps", u, v, rate_bps / 1e6)
                    self.port_stats[key] = (tx_bytes, time.time())
                    break

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        self.add_flow(datapath, 0, datapath.ofproto_parser.OFPMatch(),
                      [datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER, datapath.ofproto.OFPCML_NO_BUFFER)])

    # --- START OF MODIFIED SECTION ---
    # This is now the 5-tuple-aware packet handler
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP: return

        src_mac, dst_mac = eth.src, eth.dst
        src_dpid = datapath.id

        if src_mac not in self.host_location:
            self.host_location[src_mac] = (src_dpid, in_port)

        # Start 5-tuple parsing
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if not ip_pkt:
            self._flood(datapath, msg)
            return

        tcp_pkt = pkt.get_protocol(tcp.tcp)
        udp_pkt = pkt.get_protocol(udp.udp)

        if tcp_pkt:
            ip_proto = ip_pkt.proto
            src_ip, dst_ip = ip_pkt.src, ip_pkt.dst
            src_port, dst_port = tcp_pkt.src_port, tcp_pkt.dst_port
            flow_key_forward = (src_ip, dst_ip, ip_proto, src_port, dst_port)
            flow_key_reverse = (dst_ip, src_ip, ip_proto, dst_port, src_port)
        elif udp_pkt:
            ip_proto = ip_pkt.proto
            src_ip, dst_ip = ip_pkt.src, ip_pkt.dst
            src_port, dst_port = udp_pkt.src_port, udp_pkt.dst_port
            flow_key_forward = (src_ip, dst_ip, ip_proto, src_port, dst_port)
            flow_key_reverse = (dst_ip, src_ip, ip_proto, dst_port, src_port)
        else:
            self._flood(datapath, msg)
            return

        # Check if this specific flow is already installed
        if flow_key_forward in self.installed_flows or flow_key_reverse in self.installed_flows:
            return

        if dst_mac in self.host_location:
            dst_dpid, _ = self.host_location[dst_mac]
            
            if not (self.net.has_node(src_dpid) and self.net.has_node(dst_dpid)):
                self._flood(datapath, msg)
                return

            try:
                paths = list(nx.all_shortest_paths(self.net, source=src_dpid, target=dst_dpid, weight='weight'))
            except nx.NetworkXNoPath:
                self._flood(datapath, msg)
                return

            # --- DYNAMIC LOAD BALANCING LOGIC ---
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
                    # If loads are equal, pick one randomly to spread load
                    elif bottleneck_load == min_bottleneck_load:
                         if random.choice([True, False]):
                            best_path = path

                self.logger.info("--- New Flow %s->%s (Port %s) ---", src_ip, dst_ip, src_port)
                self.logger.info("    Path %s chosen with bottleneck load: %.2f Mbps", best_path, min_bottleneck_load / 1e6)
            else:
                best_path = paths[0]
            # --- END DYNAMIC LOGIC ---
            
            path_to_install = best_path
            
            # Install the 5-tuple symmetric flow
            self._install_symmetric_flow(path_to_install, src_mac, dst_mac, src_ip, dst_ip, ip_proto, src_port, dst_port)
            self.installed_flows.add(flow_key_forward)
            self.installed_flows.add(flow_key_reverse)

            # Tell switch to re-process the packet using the new flow rules
            out = datapath.ofproto_parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                                                       actions=[datapath.ofproto_parser.OFPActionOutput(datapath.ofproto.OFPP_TABLE)], data=msg.data)
            datapath.send_msg(out)
        else:
            self._flood(datapath, msg)

    # --- NEW 5-TUPLE FLOW INSTALLER ---
    # This is the function from the corrected p2_l2spf.py
    def _install_symmetric_flow(self, path, src_mac, dst_mac, src_ip, dst_ip, ip_proto, src_port, dst_port):
        """
        Installs symmetric flow rules (forward and reverse) for a specific 5-tuple.
        """
        parser = self.datapaths[path[0]].ofproto_parser

        # --- Install Forward Path ---
        for i, u_dpid in enumerate(path):
            dp = self.datapaths[u_dpid]
            out_port = self.host_location[dst_mac][1] if i == len(path) - 1 else self.net[u_dpid][path[i+1]]['port']

            match_args_fwd = {
                'eth_type': ether_types.ETH_TYPE_IP,
                'ipv4_src': src_ip,
                'ipv4_dst': dst_ip,
                'ip_proto': ip_proto
            }
            if ip_proto == 6: # TCP
                match_args_fwd['tcp_src'] = src_port
                match_args_fwd['tcp_dst'] = dst_port
            elif ip_proto == 17: # UDP
                match_args_fwd['udp_src'] = src_port
                match_args_fwd['udp_dst'] = dst_port

            match_fwd = parser.OFPMatch(**match_args_fwd)
            self.add_flow(dp, 1, match_fwd, [parser.OFPActionOutput(out_port)])

        # --- Install Reverse Path ---
        reverse_path = list(reversed(path))
        for i, u_dpid in enumerate(reverse_path):
            dp = self.datapaths[u_dpid]
            out_port = self.host_location[src_mac][1] if i == len(reverse_path) - 1 else self.net[u_dpid][reverse_path[i+1]]['port']

            match_args_rev = {
                'eth_type': ether_types.ETH_TYPE_IP,
                'ipv4_src': dst_ip,
                'ipv4_dst': src_ip,
                'ip_proto': ip_proto
            }
            if ip_proto == 6: # TCP
                match_args_rev['tcp_src'] = dst_port
                match_args_rev['tcp_dst'] = src_port
            elif ip_proto == 17: # UDP
                match_args_rev['udp_src'] = dst_port
                match_args_rev['udp_dst'] = src_port

            match_rev = parser.OFPMatch(**match_args_rev)
            self.add_flow(dp, 1, match_rev, [parser.OFPActionOutput(out_port)])
    # --- END OF MODIFIED SECTION ---

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
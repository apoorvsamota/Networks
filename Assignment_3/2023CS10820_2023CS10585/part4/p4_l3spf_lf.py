#!/usr/bin/env python3
# p4_l3spf_lf.py - L3 SDN Controller with Link Failure Detection and Recovery

import json
import time
import networkx as nx
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4
from ryu.topology import event

class L3RouterControllerLF(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(L3RouterControllerLF, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.net = nx.Graph()
        self.topology_links = set()

        with open('p4_config.json') as f:
            self.config = json.load(f)

        self.arp_table = {}
        for host in self.config['hosts']:
            self.arp_table[host['ip']] = host['mac']
        for switch in self.config['switches']:
            for iface in switch['interfaces']:
                self.arp_table[iface['ip']] = iface['mac']

        for link in self.config['links']:
            u, v = int(link['src'][1:]), int(link['dst'][1:])
            self.net.add_edge(u, v, weight=link['cost'])
            self.topology_links.add((min(u, v), max(u, v)))
        
        self.installed_flows = {}
        
        self.logger.info("=== L3 Router Controller with Link Failure Detection Initialized ===")
        self.logger.info("Topology Links: %s", self.topology_links)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info('Register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info('Unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_MAX)]
        self.add_flow(datapath, 0, parser.OFPMatch(), actions)

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev):
        link = ev.link
        src_dpid, dst_dpid = link.src.dpid, link.dst.dpid
        link_tuple = (min(src_dpid, dst_dpid), max(src_dpid, dst_dpid))
        
        if link_tuple not in self.topology_links:
            self.logger.info("*** LINK UP: %s <-> %s ***", src_dpid, dst_dpid)
            self.logger.info("*** Time: %s", time.strftime("%Y-%m-%d %H:%M:%S"))
            self.topology_links.add(link_tuple)
            
            if not self.net.has_edge(src_dpid, dst_dpid):
                cost = self._get_link_cost(src_dpid, dst_dpid)
                self.net.add_edge(src_dpid, dst_dpid, weight=cost)
                self.logger.info("Added edge to graph: %s <-> %s (cost=%s)", src_dpid, dst_dpid, cost)
                self._reinstall_all_flows()

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev):
        link = ev.link
        src_dpid, dst_dpid = link.src.dpid, link.dst.dpid
        link_tuple = (min(src_dpid, dst_dpid), max(src_dpid, dst_dpid))
        
        if link_tuple in self.topology_links:
            self.logger.info("*** LINK DOWN: %s <-> %s ***", src_dpid, dst_dpid)
            self.logger.info("*** Time: %s", time.strftime("%Y-%m-%d %H:%M:%S"))
            self.topology_links.discard(link_tuple)
            
            if self.net.has_edge(src_dpid, dst_dpid):
                self.net.remove_edge(src_dpid, dst_dpid)
                self.logger.info("Removed edge from graph: %s <-> %s", src_dpid, dst_dpid)
                self._reinstall_all_flows()

    def _get_link_cost(self, dpid1, dpid2):
        s1, s2 = f's{dpid1}', f's{dpid2}'
        for link in self.config['links']:
            if (link['src'] == s1 and link['dst'] == s2) or \
               (link['src'] == s2 and link['dst'] == s1):
                return link['cost']
        return 1

    def _reinstall_all_flows(self):
        self.logger.info("*** REINSTALLING ALL FLOWS ***")
        for dpid, datapath in self.datapaths.items():
            self._clear_flows(datapath)
            parser = datapath.ofproto_parser
            ofproto = datapath.ofproto
            actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_MAX)]
            self.add_flow(datapath, 0, parser.OFPMatch(), actions)
        
        for (src_ip, dst_ip) in list(self.installed_flows.keys()):
            self.logger.info("Reinstalling flow: %s -> %s", src_ip, dst_ip)
            self._install_path(src_ip, dst_ip)

    def _clear_flows(self, datapath):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        mod = parser.OFPFlowMod(datapath=datapath, command=ofproto.OFPFC_DELETE,
                                out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY,
                                match=parser.OFPMatch())
        datapath.send_msg(mod)

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

        if arp_pkt.dst_ip in self.arp_table:
            self.logger.info("ARP Request for %s. Replying.", arp_pkt.dst_ip)
            src_mac = self.arp_table[arp_pkt.dst_ip]
            dst_mac = arp_pkt.src_mac
            reply = packet.Packet()
            reply.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP, dst=dst_mac, src=src_mac))
            reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=src_mac, src_ip=arp_pkt.dst_ip,
                                      dst_mac=dst_mac, dst_ip=arp_pkt.src_ip))
            reply.serialize()
            actions = [datapath.ofproto_parser.OFPActionOutput(in_port)]
            out = datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=datapath.ofproto.OFP_NO_BUFFER,
                in_port=datapath.ofproto.OFPP_CONTROLLER,
                actions=actions, data=reply.data)
            datapath.send_msg(out)

    def _handle_ip(self, msg, pkt):
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        src_ip, dst_ip = ip_pkt.src, ip_pkt.dst

        path = self.installed_flows.get((src_ip, dst_ip))

        if not path:
            # Install forward and reverse paths
            path = self._install_path(src_ip, dst_ip)
            self._install_path(dst_ip, src_ip)

        if not path:
            self.logger.error(f"Cannot forward packet, no path exists from {src_ip} to {dst_ip}.")
            return
            
        # Determine the output port for the buffered packet
        try:
            hop_index = path.index(datapath.id)
            out_port = None
            
            # Final hop to destination host
            if hop_index == len(path) - 1:
                dst_host = next(h for h in self.config['hosts'] if h['ip'] == dst_ip)
                iface_to_host = self._get_interface_info(datapath.id, neighbor=dst_host['name'])
                if iface_to_host:
                    out_port = iface_to_host['port'] # <<< USE PORT FROM CONFIG
            # Intermediate hop to next switch
            else:
                next_hop_dpid = path[hop_index + 1]
                iface_to_next = self._get_interface_info(datapath.id, neighbor=f's{next_hop_dpid}')
                if iface_to_next:
                    out_port = iface_to_next['port'] # <<< USE PORT FROM CONFIG
            
            # If we found a valid out_port, release the buffered packet
            if out_port is not None:
                actions = [parser.OFPActionOutput(out_port)]
                data = None
                if msg.buffer_id == ofproto.OFP_NO_BUFFER:
                    data = msg.data

                out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                          in_port=in_port, actions=actions, data=data)
                datapath.send_msg(out)
            else:
                 self.logger.warning(f"Could not determine out_port for {src_ip}->{dst_ip} at s{datapath.id}.")

        except (ValueError, StopIteration, KeyError) as e:
            self.logger.warning(f"Packet from {src_ip}->{dst_ip} is on s{datapath.id} but should not be. Path is {path}. Error: {e}")

    def _install_path(self, src_ip, dst_ip):
        src_host = next((h for h in self.config['hosts'] if h['ip'] == src_ip), None)
        dst_host = next((h for h in self.config['hosts'] if h['ip'] == dst_ip), None)
        
        if not src_host or not dst_host: return None

        src_dpid = int(src_host['switch'][1:])
        dst_dpid = int(dst_host['switch'][1:])

        try:
            path = nx.shortest_path(self.net, source=src_dpid, target=dst_dpid, weight='weight')
            if self.installed_flows.get((src_ip, dst_ip)) != path:
                self.logger.info("IP Path for %s -> %s: %s", src_ip, dst_ip, path)
            self.installed_flows[(src_ip, dst_ip)] = path
        except nx.NetworkXNoPath:
            self.logger.warning("No path found in graph for %s -> %s", src_ip, dst_ip)
            if (src_ip, dst_ip) in self.installed_flows:
                del self.installed_flows[(src_ip, dst_ip)]
            return None

        # Install flow rules on all switches in the path
        for i, dpid in enumerate(path):
            if dpid not in self.datapaths: continue
            
            dp = self.datapaths[dpid]
            parser = dp.ofproto_parser
            
            actions = []
            if i == len(path) - 1: # Final hop
                iface_to_host = self._get_interface_info(dpid, neighbor=dst_host['name'])
                if iface_to_host:
                    out_port = iface_to_host['port'] # <<< USE PORT FROM CONFIG
                    actions.extend([
                        parser.OFPActionDecNwTtl(),
                        parser.OFPActionSetField(eth_src=iface_to_host['mac']),
                        parser.OFPActionSetField(eth_dst=dst_host['mac']),
                        parser.OFPActionOutput(out_port)
                    ])
            else: # Intermediate hop
                next_dpid = path[i+1]
                iface_to_next = self._get_interface_info(dpid, neighbor=f's{next_dpid}')
                next_hop_iface = self._get_interface_info(next_dpid, neighbor=f's{dpid}')
                if iface_to_next and next_hop_iface:
                    out_port = iface_to_next['port'] # <<< USE PORT FROM CONFIG
                    actions.extend([
                        parser.OFPActionDecNwTtl(),
                        parser.OFPActionSetField(eth_src=iface_to_next['mac']),
                        parser.OFPActionSetField(eth_dst=next_hop_iface['mac']),
                        parser.OFPActionOutput(out_port)
                    ])
            
            if actions:
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=dst_ip)
                self.add_flow(dp, 1, match, actions)

        return path

    def _get_interface_info(self, dpid, neighbor):
        switch = next((s for s in self.config['switches'] if s['dpid'] == dpid), None)
        if switch:
            return next((iface for iface in switch['interfaces'] if iface['neighbor'] == neighbor), None)
        return None

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(datapath.ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                idle_timeout=idle_timeout, hard_timeout=hard_timeout,
                                match=match, instructions=inst)
        datapath.send_msg(mod)
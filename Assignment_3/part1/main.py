# -*- coding: utf-8 -*-
#
# Ryu Controller Comparison (Hub vs. Learning Switch)
#
# This file implements two Ryu applications:
# 1. HubController: Forwards every packet via PacketOut (no flow rules installed).
# 2. LearningSwitch: Installs flow rules (FlowMod) on the switches after learning MACs.

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.mac import haddr_to_bin
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
import array

# --- Base Application Class for Common Functionality ---

class BaseController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(BaseController, self).__init__(*args, **kwargs)
        # mac_to_port structure: {dpid: {mac_address: port_number}}
        self.mac_to_port = {}

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """
        Setup initial table-miss flow entry (highest priority) to send packets to controller.
        This is necessary for *any* switch to start sending traffic to the controller.
        """
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        self.logger.info("Switch connected: %s", datapath.id)

        # Install table-miss flow entry (sends all unmatched packets to the controller)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, hard_timeout=0):
        """
        Installs a flow entry (OFPFFlowMod) on the switch.
        Hard timeout is used to ensure rules don't live forever, but set to 0 (permanent) for this exercise.
        """
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst, hard_timeout=hard_timeout)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst, hard_timeout=hard_timeout)
        datapath.send_msg(mod)


# --- Hub Controller Implementation (No Flow Mods) ---

class HubController(BaseController):
    """
    Implements a controller that only uses PacketOut messages.
    No flow rules are installed on the switches, forcing every packet
    to be sent to the controller for processing.
    """
    def __init__(self, *args, **kwargs):
        super(HubController, self).__init__(*args, **kwargs)
        self.logger.info("Hub Controller Initialized (NO Flow Rules)")

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, hard_timeout=0):
        # OVERRIDE: Intentionally do nothing to prevent flow rule installation
        pass

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        # Packet parsing
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # 1. Learning (store MAC-to-port mapping only in controller)
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        # self.logger.info("Learned (Hub): %s at %s port %s", src, dpid, in_port)

        # 2. Forwarding Decision
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            self.logger.debug("Hub Controller: Found destination %s on port %s. Sending PacketOut (no flow rule).", dst, out_port)
        else:
            # Destination unknown or broadcast/multicast (Flooding)
            out_port = ofproto.OFPP_FLOOD
            self.logger.debug("Hub Controller: Destination %s unknown. Flooding PacketOut (no flow rule).", dst)

        # 3. Output Action (ALWAYS use PacketOut)
        actions = [parser.OFPActionOutput(out_port)]

        # Send the packet to the port(s)
        data = msg.data
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)


# --- Learning Switch Implementation (Installs Flow Mods) ---

class LearningSwitch(BaseController):
    """
    Implements a standard learning switch.
    Installs flow rules (FlowMod) on the switches to handle subsequent traffic.
    Only first packets for a new flow are sent to the controller.
    """
    def __init__(self, *args, **kwargs):
        super(LearningSwitch, self).__init__(*args, **kwargs)
        self.logger.info("Learning Switch Initialized (Installs Flow Rules)")

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        # Packet parsing
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        dst = eth.dst
        src = eth.src
        dpid = datapath.id

        # 1. Learning (store MAC-to-port mapping)
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        # self.logger.info("Learned (Switch): %s at %s port %s", src, dpid, in_port)

        # 2. Forwarding Decision & Rule Installation
        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            actions = [parser.OFPActionOutput(out_port)]

            # Install a flow rule (FlowMod) to handle future packets
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                # IMPORTANT: Use a high priority for the specific flow rule
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
                self.add_flow(datapath, 10, match, actions, msg.buffer_id, hard_timeout=30)
                # Send the buffered packet back to the switch to be forwarded by the new rule
                return
            else:
                # If no buffer, just send the PacketOut
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
                self.add_flow(datapath, 10, match, actions, hard_timeout=30) # Install rule first
                self.logger.debug("Learning Switch: Rule installed for %s -> %s on switch %s. Sending PacketOut.", src, dst, dpid)

        else:
            # Destination unknown (Flooding)
            out_port = ofproto.OFPP_FLOOD
            actions = [parser.OFPActionOutput(out_port)]
            self.logger.debug("Learning Switch: Destination %s unknown. Flooding PacketOut.", dst)
            # NOTE: No FlowMod is installed for flooding

        # 3. Output Action (PacketOut for the current packet)
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

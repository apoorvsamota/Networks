#!/usr/bin/env python3
# p4_sdn_topo.py - Mininet topology with OVS switches for SDN controller

import json
from mininet.net import Mininet
from mininet.node import Host, OVSKernelSwitch, RemoteController
from mininet.link import TCLink
from mininet.log import setLogLevel, info

H1_IP = '10.0.12.2/24'
H2_IP = '10.0.67.2/24'

def set_if(node, ifname, ip_cidr=None, mac=None):
    """Configure interface with IP and MAC"""
    node.cmd(f'ip addr flush dev {ifname}')
    if mac:
        node.cmd(f'ip link set dev {ifname} address {mac}')
    if ip_cidr:
        node.cmd(f'ip addr add {ip_cidr} dev {ifname}')
    node.cmd(f'ip link set {ifname} up')

def build_sdn(controller_ip='127.0.0.1', controller_port=6653):
    """Build Mininet topology with OVS switches for SDN"""
    net = Mininet(
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        build=False,
        autoSetMacs=False,
        autoStaticArp=False
    )

    info('*** Adding Remote Controller\n')
    c0 = net.addController('c0', controller=RemoteController,
                          ip=controller_ip, port=controller_port)

    info('*** Adding switches\n')
    n = 6
    switches = []
    for i in range(n):
        s = net.addSwitch(f's{i+1}', dpid=f'{i+1:016x}', protocols='OpenFlow13')
        switches.append(s)

    info('*** Adding hosts\n')
    h1 = net.addHost('h1', ip='10.0.12.2/24', mac='00:00:00:00:01:02')
    h2 = net.addHost('h2', ip='10.0.67.2/24', mac='00:00:00:00:06:02')

    info('*** Creating host-switch links\n')
    # h1 -> s1
    net.addLink(h1, switches[0], intfName1='h1-eth1', intfName2='s1-eth1', bw=100)
    # h2 -> s6
    net.addLink(h2, switches[5], intfName1='h2-eth1', intfName2='s6-eth3', bw=100)

    info('*** Creating inter-switch links (ring topology)\n')
    # s1 <-> s2 (cost 20)
    net.addLink(switches[0], switches[1], intfName1='s1-eth2', intfName2='s2-eth1', bw=100)
    # s2 <-> s3 (cost 10) - This is the high bandwidth link we'll fail
    net.addLink(switches[1], switches[2], intfName1='s2-eth2', intfName2='s3-eth1', bw=100)
    # s3 <-> s6 (cost 20)
    net.addLink(switches[2], switches[5], intfName1='s3-eth2', intfName2='s6-eth1', bw=100)
    # s6 <-> s5 (cost 20)
    net.addLink(switches[5], switches[4], intfName1='s6-eth2', intfName2='s5-eth2', bw=10)
    # s5 <-> s4 (cost 20) - This is the low bandwidth backup path
    net.addLink(switches[4], switches[3], intfName1='s5-eth1', intfName2='s4-eth2', bw=10)
    # s4 <-> s1 (cost 20)
    net.addLink(switches[3], switches[0], intfName1='s4-eth1', intfName2='s1-eth3', bw=10)

    info('*** Building network\n')
    net.build()

    info('*** Starting controller\n')
    c0.start()

    info('*** Starting switches\n')
    for switch in switches:
        switch.start([c0])

    info('*** Configuring host interfaces\n')
    # h1 configuration
    h1.cmd('ip addr flush dev h1-eth1')
    h1.cmd('ip addr add 10.0.12.2/24 dev h1-eth1')
    h1.cmd('ip link set h1-eth1 up')
    h1.cmd('ip route replace default via 10.0.12.1')

    # h2 configuration
    h2.cmd('ip addr flush dev h2-eth1')
    h2.cmd('ip addr add 10.0.67.2/24 dev h2-eth1')
    h2.cmd('ip link set h2-eth1 up')
    h2.cmd('ip route replace default via 10.0.67.1')

    return net
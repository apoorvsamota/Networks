# topo_wordcount.py
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSBridge

class WordTopo(Topo):
    def build(self, num_clients=10):
        s1 = self.addSwitch('s1')
        # Server host
        self.addHost('h_srv', ip='10.0.0.1/24')
        self.addLink('h_srv', s1)
        # N client hosts
        for i in range(1, num_clients + 1):
            self.addHost(f'h_cli_{i}', ip=f'10.0.0.{i+1}/24')
            self.addLink(f'h_cli_{i}', s1)

def make_net(num_clients=10):
    return Mininet(
        topo=WordTopo(num_clients=num_clients),
        switch=OVSBridge,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True,
    )

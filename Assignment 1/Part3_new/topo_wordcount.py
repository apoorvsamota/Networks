from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSBridge

class WordTopo(Topo):
    def build(self, num_clients=10):
        s1 = self.addSwitch('s1')
        self.addHost('h_srv', ip='10.0.0.2/24')
        self.addLink('h_srv', s1)
        for i in range(1, num_clients + 1):
            h = f'h_cli_{i}'
            self.addHost(h, ip=f'10.0.0.{i+2}/24')
            self.addLink(h, s1)

def make_net(num_clients=10):
    return Mininet(
        topo=WordTopo(num_clients=num_clients),
        switch=OVSBridge,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True,
    )

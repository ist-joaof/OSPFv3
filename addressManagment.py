import commands
import ctypes.util
import struct


libc = ctypes.CDLL(ctypes.util.find_library('C'))


def get_interface_address(intf):
    intf_ip = 'fe80::/64'
    aux = commands.getoutput("ip address show dev "+ intf).split()
    for i in range(aux.index('inet6'),len(aux)):
        if 'fe80:' in aux[i]:
            intf_ip = aux[i].split('/')[0]
    return intf_ip


def set_interface_ipv6_address(intf, addr):
    commands.getoutput("ip -6 addr add " + addr + " dev " + intf)


def set_interface_ipv6_linklocal(intf, addr):
    default = "fe80::a00:27ff:fe1b:3cdf/64"
    commands.getoutput("ifconfig " + intf + "inet6 del " + default)
    commands.getoutput("ip address add dev " + intf + "scope link " + addr)


def get_interface(intf):
    ifn = libc.if_nametoindex(intf)
    ifn = struct.pack("I", ifn)
    return ifn


def get_int_scopeid(ifName):
    ip6scopeid = {}
    for line in open("/proc/net/if_inet6"):
        addr, id,_, scope, _, ifacename = line.split()
        ip6scopeid[ifacename] = int(id, 16)
        if ifacename == ifName:
            return int(id, 16)
    return 0


def add_route(route, via, metric, isNextHop):
    metric = str(metric)
    aux = commands.getoutput('ip -6 route')
    aux = aux.split('\n')
    for line in aux:
        if route in line:
            print_service('ip -6 route del ' + route)
            print_service(commands.getoutput('ip -6 route del ' + route))
    if isNextHop:
        print_service(commands.getoutput('ip -6 route add ' + route + ' dev ' + via.intfId + ' metric ' + metric))
    else:
        print_service(commands.getoutput(
            'ip -6 route add ' + route + ' via ' + via.linkLocal + ' dev ' + via.intfId + ' metric ' + metric))
    print_service('route ' + route + ' via ' + via.intfId + ' added')


def update_cost(destination, via, newcost, isnexthop):
    old = commands.getoutput('ip -6 route show ' + destination)
    old = old.split(' ')
    if old != '':
        try:
            old = old[:-(len(old)-old.index('metric'))]
        except:
            old = old[:-2]
        old = ' '.join(old)
        print_service('\nip -6 route del ' + old + ': \n\t- ' + commands.getoutput('ip -6 route del ' + old))
    if isnexthop:
        print_service('ip -6 route add ' + destination + ' dev ' + via.intfId + ' metric ' + str(newcost))
        print_service('\t- ' + commands.getoutput('ip -6 route add ' + destination + ' dev ' + via.intfId + ' metric ' + str(newcost)))
    else:
        print_service('ip -6 route add ' + destination + ' via ' + via.linkLocal + ' dev ' + via.intfId + ' metric ' + str(newcost))
        print_service('\t- ' + commands.getoutput('ip -6 route add ' + destination + ' via ' + via.linkLocal + ' dev ' + via.intfId + ' metric ' + str(newcost)))


def del_route(route, via):
    print_service('ip -6 route del ' + route + ' dev ' + via)
    print_service(commands.getoutput('ip -6 route del ' + route + ' dev ' + via))


def del_route_via(route, dev, via):
    print_service('ip -6 route del ' + route + ' via ' + via + ' dev ' + dev)
    print_service(commands.getoutput('ip -6 route del ' + route + ' via ' + via + ' dev ' + dev))


def clear_ip_config(dev, addr):
    print_service(commands.getoutput('ip -6 addr del ' + addr + ' dev ' + dev))


def clear_routing():
    routes = commands.getoutput('ip -6 route')
    routes = routes.split('\n')
    for route in routes:
        if route[0:4] == 'fe80':
            continue
        else:
            print_service(commands.getoutput('ip -6 route del ' + route))


def show_route():
    aux = commands.getoutput('ip -6 route show')
    aux = aux.split('\n')
    routeTable = ''
    for line in aux:
        if('fe80' in line) or ('default' in line):
            continue
        else:
            routeTable += line + '\n'
    print_service(routeTable)


def trim_ip(address, length):
    slots = int(length) / 16
    aux = address.split(':')
    out = aux[0]
    for i in range(1, slots):
        out += ':' + aux[i]
    return out + '::'


def print_service(input):
    if False:
        print(input)
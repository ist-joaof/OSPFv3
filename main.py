import ctypes.util
import commands
import dijkstraManager
import overlayLSDB
import interface
import linkStateDatabase
import addressManagment
import datetime


libc = ctypes.CDLL(ctypes.util.find_library('C'))

global_db = None
OSPF_GROUP_ADDRESS = 'ff02::5'


class OspfDb:
    def __init__(self, proccess, routerId):
        self.ospf_process_id = proccess
        self.routerId = routerId
        self.routerOptions = 0x33
        self.interfaceList = {}  # {interfaceID: intf}
        self.intfNbr = {'ens33': 1, 'ens36': 2, 'ens37': 3, 'eth0': 1, 'eth1': 2, 'eth2': 3}
        self.addressList = []  # [addr1,...]
        self.lsdbs = {}  # {aread: lsdb}
        self.routingGraph = {}  # {area: routingGraph}
        self.overlayGraph = None
        self.isInterArea = False
        self.overlayLsdb = overlayLSDB.OverlayLSDB(self)
        self.localRoutingTable = {}  # {prefix: Route()}
        self.debug = False

    def add_interface(self, intfid, area, cost):
        newRouteManager = False
        if area not in self.lsdbs.keys():
            lsdb = linkStateDatabase.LSDB(area, self)
            self.lsdbs[area] = lsdb
            newRouteManager = True
        else:
            lsdb = self.lsdbs[area]
        intf = interface.ActiveInterface(intfid, area, self, self.isInterArea, cost, self.intfNbr[intfid])
        self.interfaceList[intf.intfId] = intf
        lsdb.add_interface(intf)
        if newRouteManager:
            dijkstra = dijkstraManager.DijkstraManager(self.routerId, self, area)
            self.routingGraph[area] = dijkstra
            if len(self.lsdbs) > 1:
                self.update_is_inter_area(True)
                refresh_routing(area)

    def get_interface(self, intfId):
        try:
            return self.interfaceList[intfId]
        except:
            print('Invalid interface ID')
            return None

    def get_intf_address_for_neighbor(self, neighbor, area):
        for intf in self.interfaceList.values():
            if intf.areaId == area:
                if neighbor in intf.neighborList.keys():
                    return intf.neighborList[neighbor].neighborAddress
        return None

    def get_dbs(self, area):
        out = self.get_lsdb(area).dbs.copy()
        out.update(self.overlayLsdb.dbs)
        return out

    def update_is_inter_area(self, newState):
        if self.isInterArea != newState:
            self.isInterArea = newState
            if newState:  # if is inter-area
                self.overlayLsdb.create_route_manager()
                self.overlayLsdb.create_abrls()
                for lsdb in self.lsdbs.values():
                    lsdb.set_interarea(newState)
            else:  # if is no longer inter-area
                for lsdb in self.lsdbs.values():
                    lsdb.set_interarea(newState)
                list = self.overlayLsdb.kill_self_overlay_lsas()
                if len(list) > 0:
                    self.overlayLsdb.send_overlay_dead_lsas(list)

    def router_is_inter_area(self):
        return self.isInterArea

    def get_router_options(self):
        return self.routerOptions

    def get_router_id(self):
        return self.routerId

    def list_neighbors(self):
        print('\nList of router neighbors:')
        for intf in self.interfaceList.values():
            print('Interface ' + intf.intfId + ':')
            intf.print_neighbors()

    def list_ospf_interfaces(self):
        print('\nList of router Interfaces:')
        for intf in self.interfaceList.keys():
            print('\n ' + intf)

    def store_address(self, address):
        address = address.split('::')[0] + ':'
        self.addressList.append(address)

    def remove_addres(self, address):
        self.addressList.remove(address)

    def clear_ospfdb(self):
        self.addressList = []
        self.LSDBs = {}
        self.routingGraph = {}
        self.overlayGraph = None
        self.localRoutingTable = {}

    def get_lsdb(self, areaID):
        return self.lsdbs[areaID]

    def clear_lsdb(self, areaId):
        try:
            del self.lsdbs[areaId]
        except:
            print 'Failed to delete lsdb for area ' + str(areaId)

    def add_route(self, destination, cost, area):
        if destination not in self.localRoutingTable.keys():
            newRoute = Route(destination, cost, area)
            self.localRoutingTable[destination] = newRoute
            return True
        elif area != self.localRoutingTable[destination].area:
            if self.localRoutingTable[destination].cost >= cost:
                destination = self.localRoutingTable[destination]
                destination.backup[destination.area] = destination.cost
                destination.area = area
                destination.cost = cost
                return True
            else:
                destination = self.localRoutingTable[destination]
                destination.backup[area] = cost
                return False
        else:
            self.localRoutingTable[destination].cost = cost
            return True

    def remove_route(self, destination, area):
        if destination in self.localRoutingTable.keys():
            if self.localRoutingTable[destination].area == area:
                dest = self.localRoutingTable[destination]
                if len(dest.backup) > 0:
                    bestCost = 100
                    newArea = None
                    for area in dest.backup.keys():
                        if dest.backup[area] < bestCost:
                            bestCost = dest.backup[area]
                            newArea = area
                    if newArea is not None:
                        del dest.backup[newArea]
                        dest.cost = 1000
                        refresh_routing(newArea)
                    else:
                        refresh_routing(area)
                return True
            else:
                dest = self.localRoutingTable[destination]
                del dest.backup[area]
                return False
        else:
            print '\n\n\t' + str(destination) + '\n\n'
            return False

    def update_route_cost(self, destination, cost, area):
        if self.localRoutingTable[destination].area == area:
            route = self.localRoutingTable[destination]
            route.update_cost(cost)


class Route:
    def __init__(self, dest, cost, area):
        self.dest = dest
        self.cost = cost
        self.area = area
        self.backup = {}  # area: cost

    def update_cost(self, newCost):
        self.cost = newCost


def show(options, ospfDb):
    lsdbs = ospfDb.lsdbs
    overlayLsdb = ospfDb.overlayLsdb
    routingGraph = ospfDb.routingGraph

    if options[1] == 'interface':
        intf = ospfDb.get_interface(options[2])
        if intf != None:
            intf.get_interface_address()
    elif options[1] == 'neighbors':
        ospfDb.list_neighbors()
    elif options[1] == 'interfaces':
        ospfDb.list_ospf_interfaces()
    elif options[1] == 'lsdb':
        for lsdb in lsdbs.values():
            print
            lsdb.print_lsdb()
    elif options[1] == 'overlay':
        overlayLsdb.print_overlay_lsdb()
    elif options[1] == 'routerlsas':
        for lsdb in lsdbs.values():
            print 'Area ' + str(lsdb.area)
            lsdb.print_lsas(linkStateDatabase.ROUTER_LSA)
    elif options[1] == 'networklsas':
        for lsdb in lsdbs.values():
            print 'Area ' + str(lsdb.area)
            lsdb.print_lsas(linkStateDatabase.NETWORK_LSA)
    elif options[1] == 'linklsas':
        for lsdb in lsdbs.values():
            print 'Area ' + str(lsdb.area)
            lsdb.print_lsas(linkStateDatabase.LINK_LSA)
    elif options[1] == 'intra-arealsas':
        for lsdb in lsdbs.values():
            print 'Area ' + str(lsdb.area)
            lsdb.print_lsas(linkStateDatabase.INTRA_AREA_PREFIX_LSA)
    elif options[1] == 'inter-arealsas':
        for lsdb in lsdbs.values():
            print 'Area ' + str(lsdb.area)
            lsdb.print_lsas(linkStateDatabase.INTER_AREA_PREFIX_LSA)
    elif options[1] == 'abrlsas':
        overlayLsdb.print_lsas(overlayLSDB.ABR_LSA)
    elif options[1] == 'prefixlsas':
        overlayLsdb.print_lsas(overlayLSDB.PREFIX_LSA)
    elif options[1] == 'asbrlsas':
        overlayLsdb.print_lsas(overlayLSDB.ASBR_LSA)
    elif options[1] == 'graph':
        if options[2] == 'local':
            if len(options) == 3:
                for entry in routingGraph.values():
                    entry.print_graph()
            else:
                for entry in routingGraph.values():
                    entry.print_graph_nodes()
        else:
            if len(options) == 3:
                ospfDb.overlayGraph.print_graph()
            else:
                ospfDb.overlayGraph.printGraphNodes()
    elif options[1] == 'routes':
        pass
        addressManagment.show_route()
    elif options[1] == 'nodes':
        if options[2] == 'local':
            for entry in routingGraph.values():
                entry.print_nodes()
        else:
            ospfDb.overlayGraph.print_nodes()
    elif options[1] == 'edges':
        if options[2] == 'local':
            for area in ospfDb.routingGraph.values():
                area.print_edges()
            else:
                ospfDb.overlayGraph.graph.print_edges()


def set_ip_address(options, ospfDb):
    intf = ospfDb.get_interface(options[2])
    if intf is None:
        return
    if 'fe80' in options[3]:
        addressManagment.set_interface_ipv6_linklocal(options[2], options[3])
    else:
        ospfDb.store_address(options[3].split('::')[0] + ':')
        addressManagment.set_interface_ipv6_address(options[2], options[3])
        intf.set_interface_full_address(
            options[3])


def change_interface_cost(intf, cost, ospfDb):
    intf = ospfDb.get_interface(intf)
    if intf == None:
        return
    else:
        intf.change_interface_cost(cost)


def list_help():
    print('Show interface IPv6 address:\n\t show interface <interfaceID>')
    print('Set interface IPv6 Link-local address:\n\t set interface <interfaceID> <address>')
    print('Set interface IPv6 address:\n\t set interface <interfaceID> <address>')
    print('Change interface cost:\n\t change <interfaceID> <cost>')
    print('Start ospf process:\n\t start ospf <processID>')
    print('Activate ospf interface:\n\t interface <interfaceID> area <areaID>')
    print('Show all active neighbors:\n\t show neighbors')
    print('Show ospf interfaces:\n\t show interfaces')
    print('Show ospf Link State Database:\n\t show lsdb')
    print('Show ospf Overlay Link State Database:\n\t show overlay')
    print('Show Router LSAs:\n\t show routerlsas')
    print('Show Network LSAs:\n\t show networklsas')
    print('Show Link LSAs:\n\t show linklsas')
    print('Show Intra-Area LSAs:\n\t show intra-arealsas')
    print('Show Inter-Area Prefix LSAs:\n\t show inter-arealsas')
    print('Show Area Border Router LSAs:\n\t show abrlsas')
    print('Show Prefix LSAs:\n\t show prefixlsas')
    print('Show Route Table:\n\t show route')
    print('Disable interface:\n\t shutdown <intfID>')
    print('List help:\n\t help or ?')
    print('Exit:\n\t exit')


def menu(option, ospfDb):
    options = option.split()
    if len(options) == 0:
        print('\nInvalid command!\n')
        return
    elif options[0] == 'exit':
        exit()
    elif options[0] == 'show':
        show(options, ospfDb)
    elif options[0] == 'set':
        set_ip_address(options, ospfDb)
    elif options[0] == 'start' and options[1] == 'ospf':
        start_ospf(options[2])
    elif options[0] == 'help' or options[0] == '?':
        list_help()
    elif options[0] == 'debug':
        ospfDb.debug = not ospfDb.debug
        print("debugging")
    elif options[0] == 'interface':
        ospfDb.add_interface(options[1], options[3], options[4])
    elif options[0] == 'change':
        change_interface_cost(options[1], int(options[2]), ospfDb)
    elif options[0] == 'shutdown':
        shutdown_interface(options[1], ospfDb)
    elif options[0] == 'refresh':
        refresh_routing(options[1])
    elif options[0] == 'run':
        run_command(options[1:])
    elif len(options) == 1:
        quick_start(options[0])
    else:
        print('\nInvalid command!\n')
        return


def quick_start(routerId):
    global global_db
    routerId = routerId + '.' + routerId + '.' + routerId + '.' + routerId
    if routerId == '1.1.1.1':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '1.1.1.1', '4')
        global_db.add_interface('eth1', '1.1.1.1', '1')
        global_db.add_interface('eth2', '1.1.1.1', '5')
    elif routerId == '2.2.2.2':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '1.1.1.1', '4')
        global_db.add_interface('eth1', '2.2.2.2', '1')
    elif routerId == '3.3.3.3':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '1.1.1.1', '1')
        global_db.add_interface('eth1', '3.3.3.3', '1')
        global_db.add_interface('eth2', '3.3.3.3', '4')
    elif routerId == '4.4.4.4':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '1.1.1.1', '5')
        global_db.add_interface('eth1', '3.3.3.3', '1')
        global_db.add_interface('eth2', '3.3.3.3', '1')
    elif routerId == '5.5.5.5':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '2.2.2.2', '1')
        global_db.add_interface('eth1', '4.4.4.4', '1')
    elif routerId == '6.6.6.6':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '3.3.3.3', '4')
        global_db.add_interface('eth1', '3.3.3.3', '1')
        global_db.add_interface('eth2', '4.4.4.4', '1')
    elif routerId == '7.7.7.7':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '4.4.4.4', '1')
        global_db.add_interface('eth1', '4.4.4.4', '1')
    elif routerId == '8.8.8.8':
        global_db = OspfDb(10, routerId)
        global_db.add_interface('eth0', '4.4.4.4', '1')
        global_db.add_interface('eth1', '4.4.4.4', '1')
    else:
        return


def run_command(command):
    command = ' '.join(command)
    print commands.getoutput(command)
    print '\nRouter_Linux: '


def refresh_routing(area):
    global global_db
    lsdb = global_db.lsdbs[area]
    lsdb.routeManager.refresh_routing()


def shutdown_interface(intfId, db):
    intf = db.interfaceList[intfId]
    del db.interfaceList[intfId]
    intf.shutdown()


def start_ospf(proccessId):
    global global_db
    routerID = raw_input('Router ID: ')
    global_db = OspfDb(proccessId, routerID)
    print('\nOSPF protocol started!\n')


def exit():
    global global_db
    for intf in global_db.interfaceList.values():
        intf.clear_ip_config()
    addressManagment.clear_routing()
    quit()


"""Main"""
commands.getoutput('sysctl -w net.ipv6.conf.all.forwarding=1')
while True:
    command = raw_input('Router_Linux: ')
    print str(datetime.datetime.now().strftime('%H:%M:%S.%f') + ' ' + command)
    menu(command, global_db)

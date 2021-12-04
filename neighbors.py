import time
import threading
import interface
import socket
import packetManager
import datetime

state = {'Down':0,
         'Attempt':1,
         'Init':2,
         '2-Way':3,
         'ExStart':4,
         'ExChange':5,
         'Loading':6,
         'Full':7}


class Neighbor:
    def __init__(self, routerDeadInterval, neighborId, nprio, addr, options, dr, bdr, intfId, ospfDb, areaId, intf):
        self.active = True
        self.state = 0
        self.inactivityTimer = int(routerDeadInterval)
        self.masterSlave = 0  # 0 = Master, 1 = Slave
        self.ddSequenceNumber = 0
        self.neighborId = neighborId
        self.neighborPriority = int(nprio)
        self.neighborAddress = addr
        self.neighborOptions = options
        self.neighborDesignatedRouter = dr
        self.neighborBackupDesignatedRouter = bdr
        self.neighborIntfId = int(intfId)
        self.neighbors = []
        self.areaId = areaId
        self.intf = intf

        self.ospfDb = ospfDb

        self.dbDescriptionPacket = None # change from (header, data) to packet

        self.exStart = None  # None -> exStartState() not running
        self.exChange = None  # None -> exChangeState() not running
        self.loading = None  # None -> loadingState() not running

        self.thread = threading.Thread(target=self.run, args=())
        self.thread.daemon = True
        self.thread.start()

    def run(self):
        self.state = state['Attempt']
        while self.active:
            self.inactivityTimer -= 1
            if self.inactivityTimer == 0:
                self.neighbor_down()
                break
            time.sleep(1)

    def neighbor_down(self):
        if not self.intf.active:
            return
        print datetime.datetime.now().strftime('%H:%M:%S.%f') + ' neighbor ' + str(self.neighborId) + ' is down on ' + str(self.intf.intfId)
        self.state = state['Down']
        lsas = []
        killlsas = []
        lsdb = self.ospfDb.lsdbs[self.areaId]
        wasDR = self.intf.is_dr()
        del self.intf.neighborList[self.neighborId]
        idx = self.intf.remove_adjacency(self.neighborId)
        if idx != None:
            lsas.append(idx)
        if len(self.intf.neighborList) >= 1:
            if self.neighborId == self.intf.designatedRouter or self.neighborId == self.intf.backupDesignatedRouter:
                if self.neighborId == self.intf.backupDesignatedRouter:
                    self.intf.backupDesignatedRouter = '0.0.0.0'
                else:
                    self.intf.designatedRouter = self.intf.backupDesignatedRouter
                    if self.intf.is_dr():
                        self.intf.state = interface.STATES['DR']
                    self.intf.backupDesignatedRouter = '0.0.0.0'
                interface.elect_dr_bdr(self.intf, False)

                idx = lsdb.update_self_router_lsa_update_dr(self.intf)
                lsas.append(idx)
        else:   # if no more neighbors
            if not self.intf.is_dr():
                self.intf.designatedRouter = self.ospfDb.routerId
                self.intf.designatedRouterIntf = '0.0.0.' + str(self.intf.intfNumber)
                self.intf.state = interface.STATES['DR']
                self.intf.backupDesignatedRouter = '0.0.0.0'
            else:
                self.intf.backupDesignatedRouter = '0.0.0.0'
        if self.intf.is_dr():
            if wasDR:
                if len(self.intf.neighborList) > 0:
                    idx = lsdb.update_self_network_lsa(self.intf, self.neighborId)
                    lsas.append(idx)
                else:
                    idx = lsdb.kill_self_network_lsa(self.intf)
                    killlsas.append(idx)
            else:
                if len(self.intf.neighborList) > 0:
                    idx = lsdb.create_network_lsa(self.intf)
                    lsas.append(idx)
        else:
            pass # if not DR no furhter action needed
        if len(lsas) > 0 or len(killlsas) > 0:
            for intf in lsdb.interfaceList:
                intf.updateManager.send_multicast_update(intf, lsas)
                intf.updateManager.send_multicast_kill_lsa(intf, killlsas)

    def neighbor_is_full(self):
        if self.state == state['Full']:
            return True
        else:
            return False

    def hello_received(self, intf, packet):
        if self.state == state['Attempt']:
            self.state = state['Init']
        if self.state == state['Down']:
            self.state = state['Init']
        intf.hasNeighbor = True
        self.update_neighbor(packet)
        self.inactivityTimer = intf.routerDeadInterval

    def update_neighbor(self, packet):
        self.neighborDesignatedRouter = packet.designatedRouter
        self.neighborBackupDesignatedRouter = packet.backupDesignatedRouter
        self.neighbors = packet.neighbors

    def db_desc_received(self, packet, intf):
        self.dbDescriptionPacket = packet
        if self.exChange is not None:
            self.exChange.receive(packet)
        elif self.exStart is not None:
            self.exStart.receive(packet, intf)
        elif self.state < state['ExStart']:
            self.state = state['ExStart']
            self.dd_seq_manager()
            self.exStart = ExStartState(intf, self)

    def ls_req_received(self, packet, intf):
        intf.updateManager.send_unicast_update(self.neighborAddress, intf, packet.requests)

    def ls_update_received(self, header, intf, destination):
        if self.loading is not None:
            self.loading.acknowledge()
        intf.updateManager.acknowledge_update(intf, header, destination)

    def two_way_received(self, intf):
        if self.neighborId == self.neighborDesignatedRouter or self.neighborId == self.neighborBackupDesignatedRouter or intf.state >= 5:  # 5:BackupDR, 6:DR
            self.state = state['ExStart']
            self.dd_seq_manager()
            self.exStart = ExStartState(intf, self)
        else:
            self.state = state['2-Way']

    def dd_seq_manager(self):
        if self.ddSequenceNumber == 0:
            self.ddSequenceNumber = int(time.time() % 1000)
        else:
            self.ddSequenceNumber += 1

    def negotiation_done(self, intf, ddSequence, packet):
        self.state = state['ExChange']
        self.exStart = None
        self.exChange = ExChangeState(intf, self, ddSequence, packet)

    def exchange_done(self, full, intf):
        self.exChange = None
        if full:
            self.state = state['Full']
            print datetime.datetime.now().strftime('%H:%M:%S.%f') + ' Loading done, neighbor' + str(
                self.neighborId) + ' is fully adjacent'
            intf.hasFullAdjacencies = True
        else:
            self.state = state['Loading']
            self.loading = LoadingState(intf, self)

    def loading_done(self, intf):
        self.loading = None
        self.state = state['Full']
        print datetime.datetime.now().strftime('%H:%M:%S.%f') + ' Loading done, neighbor' + str(
            self.neighborId) + ' is fully adjacent'
        if intf.is_dr():
            intf.create_network_lsa()


class ExStartState:
    def __init__(self, interface, neighbor):
        self.stop = False
        self.addrInfo = (neighbor.neighborAddress, 0, 0, interface.scopeId)
        self.sender_socket = socket.socket(socket.AF_INET6,socket.SOCK_RAW,socket.IPPROTO_RAW)
        self.sender_thread = threading.Thread(target=self.send, args=(interface, neighbor))
        self.sender_thread.daemon = True

        self.sender_thread.start()

    def send(self, interface, neighbor):
        ims = int(0b00000111)
        while not self.stop:
            packet = packetManager.build_db_description_packet(0,False,interface,neighbor,ims, self.addrInfo[0])
            self.sender_socket.sendto(packet, self.addrInfo)
            time.sleep(interface.rxmtInterval)

    def receive(self, packet, intf):
        neighborId = packet.ospfHeader.routerId
        neighbor = intf.neighborList[neighborId]
        if neighborId > intf.routerId:
            neighbor.masterSlave = 1
            ddSeq = packet.ddSequence
            self.stop = True
            neighbor.negotiation_done(intf, ddSeq, packet)
        else:
            ddSeq = neighbor.ddSequenceNumber
            if packet.ims['MS'] != '1':
                self.stop = True
                neighbor.negotiation_done(intf,ddSeq,packet)


class ExChangeState():
    def __init__(self, interface, neighbor, ddSeq, packet):
        self.stop = False
        self.acknowledged = False
        self.received_packet = packet    # changed from [header, data] to packet
        self.ddSeq = ddSeq
        self.addrInfo = (neighbor.neighborAddress, 0, 0, interface.scopeId)
        self.sender_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)

        self.master_thread = threading.Thread(target=self.master, args=(interface, neighbor))
        self.master_thread.daemon = True
        self.slave_thread = threading.Thread(target=self.slave, args=(interface, neighbor))
        self.slave_thread.daemon = True

        self.run(neighbor)

    def run(self, neighbor):
        self.master = False
        if neighbor.masterSlave == 0:   # router is master
            self.master = True
            self.master_thread.start()
        else:                           # router is slave
            self.slave_thread.start()

    def master(self, interface, neighbor):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        while True:
            try:
                neighbor.dbDescriptionPacket.lsas.values()[0]
                packet = neighbor.dbDescriptionPacket
                break
            except:
                pass
        packetManager.store_ls_requests(lsdb, packet)
        self.received_packet = 0
        ims = int(0b0011)
        self.ddSeq += 1
        packet = packetManager.build_db_description_packet(self.ddSeq, False, interface, neighbor, ims, neighbor.neighborAddress)     # TODO packetManager.build_db_description_packet
        while not self.acknowledged:
            self.sender_socket.sendto(packet, self.addrInfo)
            time.sleep(interface.rxmtInterval)
        self.acknowledged = False
        self.ddSeq += 1
        ims = int(0b0001)
        packet = packetManager.build_db_description_packet(self.ddSeq, True, interface, neighbor, ims, neighbor.neighborAddress)
        while not self.acknowledged:
            self.sender_socket.sendto(packet, self.addrInfo)
            time.sleep(interface.rxmtInterval)
        self.acknowledged = False
        self.ddSeq += 1
        self.received_packet = 0

        if len(lsdb.lsRequests.keys()) > 0:
            neighbor.exchange_done(False, interface)
        else:
            neighbor.exchange_done(True, interface)

    def slave(self, interface, neighbor):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        ims = int(0b010)
        packet = packetManager.build_db_description_packet(self.ddSeq, False, interface, neighbor, ims, neighbor.neighborAddress)
        self.acknowledged = False
        i = 0
        while True:
            if self.acknowledged:
                try:
                    neighbor.dbDescriptionPacket.lsas.values()[0]
                    break
                except:
                    self.acknowledged = False
            self.sender_socket.sendto(packet, self.addrInfo)
            time.sleep(interface.rxmtInterval)
        packet = self.received_packet
        packetManager.store_ls_requests(lsdb, packet)  # stores requests to be made
        self.ddSeq = packet.ddSequence
        self.received_packet = 0

        if len(lsdb.lsRequests.keys()) > 0:
            neighbor.exchange_done(False, interface)
        else:
            neighbor.exchange_done(True, interface)

        self.acknowledged = False
        i = 0
        ims = int(0b000)
        while not self.acknowledged and i < 3:
            ddSeq = neighbor.dbDescriptionPacket.ddSequence
            packet = packetManager.build_db_description_packet(ddSeq, True, interface, neighbor, ims, neighbor.neighborAddress)
            self.sender_socket.sendto(packet, self.addrInfo)
            i += 1
            time.sleep(5)

    def receive(self, packet):
        if self.master:
            if packet.ddSequence == self.ddSeq:
                self.acknowledged = True
                self.received_packet = packet
        else:
            if packet.ddSequence != self.ddSeq:
                self.acknowledged = True
                self.received_packet = packet


class LoadingState():
    def __init__(self, interface, neighbor):
        self.acknowledged = False

        self.addrInfo = (neighbor.neighborAddress, 0, 0, interface.scopeId)
        self.sender_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)

        self.sender_thread = threading.Thread(target=self.sender,args=(interface,neighbor))
        self.sender_thread.daemon = True

        self.sender_thread.start()

    def sender(self, interface, neighbor):
        packet = packetManager.build_ls_request(interface, neighbor)
        while not self.acknowledged:
            self.sender_socket.sendto(packet, self.addrInfo)
            time.sleep(interface.rxmtInterval)
        neighbor.loading_done(interface)
        interface.add_adjacency(neighbor.neighborId)
        time.sleep(1)

    def acknowledge(self):
        self.acknowledged = True
# A bring-your-own-I/O implementation of Channel Access
# in the spirit of http://sans-io.readthedocs.io/
import ctypes
import itertools
from io import BytesIO
from collections import defaultdict, deque
from ._commands import *
from ._dbr_types import *
from ._state import *


CLIENT_VERSION = 13
_MessageHeaderSize = ctypes.sizeof(MessageHeader)
_ExtendedMessageHeaderSize = ctypes.sizeof(ExtendedMessageHeader)

def parse_command_response(header):
    ...    


def extend_header(header):
    "Return True if header should be extended."
    return header.payload_size == 0xFFFF and header.data_count == 0


class Server:
    "An object encapsulating the state of an EPICS Server."
    def __init__(self):
        self._sid_counter = itertools.count(0)

    def new_sid(self):
        return next(self._sid_counter)

    def version(self):
        return VersionResponse(...)

    def create_channel(self, name, cid):
        return CreateResponse(..., sid)

    def search(self, name):
        return SearchResponse(...)


class VirtualCircuit:
    def __init__(self, address, priority):
        self.our_role = CLIENT
        self.their_role = SERVER
        self.address = address
        self.priority = priority
        self._state = CircuitState()
        self._data = bytearray()

    def send(self, command):
        self._process_command(self.our_role, command)
        return command

    def recv(self, byteslike):
        self._data += byteslike

    def _process_command(self, role, command):
        # All commands go through here.
        self._state.process_command(self.our_role, type(command))
        self._state.process_command(self.their_role, type(command))

    def next_command(self):
        header_size = _MessageHeaderSize
        if len(self._data) >= header_size:
            header = MessageHeader.from_buffer(self._data)
        else:
            return NEEDS_DATA
        if extend_header(header):
            header_size = _ExtendedMessageHeaderSize
            if len(self._data) >= header_size:
                header = ExtendedMessageHeader.from_buffer(self._data)
            else:
                return NEEDS_DATA
        payload_bytes = b''
        if header.payload_size > 0:
            payload = []
            total_size = header_size + header.payload_size
            if len(self._data) < total_size:
                return NEEDS_DATA
        _class = Commands[str(self.their_role)][header.command]
        command = _class.from_wire(header, payload_bytes)
        self._process_command(self.our_role, command)
        return command


class Client:
    "An object encapsulating the state of an EPICS Client."
    PROTOCOL_VERSION = 13

    def __init__(self):
        self.our_role = CLIENT
        self.their_role = SERVER
        self._names = {}  # map known names to (host, port)
        self._circuits = {}  # keyed by (address, priority)
        self._channels = {}  # map cid to Channel
        self._cid_counter = itertools.count(0)
        self._datagram_inbox = deque()
        # self._datagram_outbox = deque()

    def new_channel(self, name, priority=0):
        cid = next(self._cid_counter)
        circuit = None
        channel = Channel(name, circuit, cid, name, priority)
        self._channels[cid] = channel
        # If this Client has searched for this name and already knows its
        # host, skip the Search step and create a circuit.
        # if name in self._names:
        #     circuit = self._circuits[(self._names[name], priority)]
        msg = SearchRequest(name, cid, self.PROTOCOL_VERSION)
        # self._datagram_outbox.append(msg)
        return channel

    def send_broadcast(self, command):
        "Return bytes to broadcast over UDP socket."
        self._process_command(self.our_role, command)
        return command

    def recv_broadcast(self, byteslike, address):
        "Cache but do not process bytes that were received via UDP broadcast."
        self._datagram_inbox.append((byteslike, address))

    def next_command(self):
        "Process cached received bytes."
        byteslike, (host, port) = self._datagram_inbox.popleft()
        command = read_bytes(byteslike, self.their_role)
        # For UDP, monkey-patch the address on as well.
        command.address = (host, port)
        self._process_command(self.their_role, command)
        return command

    def _process_command(self, role, command):
        # All commands go through here.
        if isinstance(command, SearchRequest):
            # Update the state machine of the pertinent Channel.
            cid = command.header.parameter2
            chan = self._channels[cid]
            chan._state.process_command(self.our_role, type(command))
            chan._state.process_command(self.their_role, type(command))
        elif isinstance(command, SearchResponse):
            # Update the state machine of the pertinent Channel.
            chan = self._channels[command.header.parameter2]
            chan._state.process_command(self.our_role, type(command))
            chan._state.process_command(self.their_role, type(command))
            # Identify an existing VirtcuitCircuit with the right address and
            # priority, or create one.
            self._names[chan.name] = command.address
            key = (command.address, chan.priority)
            try:
                circuit = self._circuits[key]
            except KeyError:
                circuit = VirtualCircuit(*key)
                self._circuits[key] = circuit
            chan.circuit = circuit


class Channel:
    "An object encapsulating the state of the EPICS Channel on a Client."
    def __init__(self, client, circuit, cid, name, priority=0):
        self._cli = client
        self._circuit = circuit
        self.cid = cid
        self.name = name
        self.priority = priority
        self._state = ChannelState()
        self.native_data_type = None
        self.native_data_count = None
        self.sid = None
        self._requests = deque()

    @property
    def circuit(self):
        return self._circuit

    @circuit.setter
    def circuit(self, circuit):
        if self._circuit is None:
            self._circuit = circuit
        else:
            raise RuntimeError("circuit may only be set once")

    def create(self, native_data_type, native_data_count, sid):
        "Called by the Client when the Server gives this Channel an ID."
        self.native_data_type = native_data_type
        self.native_data_count = native_data_count
        self.sid = sid

    def next_request(self):
        # Do this logic based on a `state` explicit advanced by the Client.
        if self._cli[circuit] != CONNECTED:
            return self.circuit, VersionRequest(self.priority, CLIENT_VERSION)
        elif self.sid is None:
            return self.circuit, CreateRequest(cid, CLIENT_VERSION, self.name)
        return None
     
    def read(self, data_type=None, data_count=None):
        if data_type is None:
            data_type = self.native_data_type
        if data_count is None:
            data_count = self.native_data_count
        return self.circuit, ReadRequest(...)

    def write(self, data):
        return self.circuit, WriteRequest(...)

    def subscribe(self, data_type=None, data_count=None):
        if data_type is None:
            data_type = self.native_data_type
        if data_count is None:
            data_count = self.native_data_count
        return self.circuit, SubscribeRequest(...)

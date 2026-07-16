import time
from tango import AttrQuality, AttrWriteType, DevState, Attr, CmdArgType, UserDefaultAttrProp
from tango.server import Device, attribute, command, DeviceMeta
from tango.server import class_property, device_property, run
import os
import json
from json import JSONDecodeError
import tempfile
import canopen
from threading import Thread, Lock, Event

class Canopen(Device, metaclass=DeviceMeta):
    network = None
    node = None
    dynamic_attribute_indices = {}
    # sdo transfers are request/response over the shared bus, so the health monitor and the tango
    # request threads must not run one at the same time or their responses would interleave
    client_lock = Lock()
    _health_thread = None
    _health_stop = None

    network_channel = device_property(dtype=str, default_value="can0")
    network_interface = device_property(dtype=str, default_value="socketcan")
    network_bitrate = device_property(dtype=int, default_value=0)
    eds_file = device_property(dtype=str, default_value="")
    node_id = device_property(dtype=int, default_value=1)
    init_dynamic_attributes = device_property(dtype=str, default_value="")
    health_check_interval = device_property(dtype=float, default_value=2.0)

    @attribute
    def time(self):
        return time.time()

    @command(dtype_in=str)
    def add_dynamic_attribute(self, name, index, 
            variable_type_name="DevString", min_value="", max_value="",
            unit="", write_type_name="", min_alarm="", max_alarm="",
            min_warning="", max_warning=""):
        if index == "":
            return
        prop = UserDefaultAttrProp()
        variableType = self.stringValueToVarType(variable_type_name)
        writeType = self.stringValueToWriteType(write_type_name)
        if min_value != "" and min_value != max_value:
            prop.set_min_value(min_value)
        if max_value != "" and min_value != max_value:
            prop.set_max_value(max_value)
        if unit != "": prop.set_unit(unit)
        if min_alarm != "": prop.set_min_alarm(min_alarm)
        if max_alarm != "": prop.set_max_alarm(max_alarm)
        if min_warning != "": prop.set_min_warning(min_warning)
        if max_warning != "": prop.set_max_warning(max_warning)
        attr = Attr(name, variableType, writeType)
        attr.set_default_properties(prop)
        self.add_attribute(attr, r_meth=self.read_dynamic_attr, w_meth=self.write_dynamic_attr)
        self.set_change_event(name, True, False)
        self.dynamic_attribute_indices[name] = index
        self.info_stream("Added dynamic attribute %s %s", index, name)

    def stringValueToVarType(self, variable_type_name) -> CmdArgType:
        return {
            "DevBoolean": CmdArgType.DevBoolean,
            "DevLong": CmdArgType.DevLong,
            "DevDouble": CmdArgType.DevDouble,
            "DevFloat": CmdArgType.DevFloat,
            "DevString": CmdArgType.DevString,
        }.get(variable_type_name, CmdArgType.DevString)

    def stringValueToWriteType(self, write_type_name) -> AttrWriteType:
        return {
            "READ": AttrWriteType.READ,
            "WRITE": AttrWriteType.WRITE,
            "READ_WRITE": AttrWriteType.READ_WRITE,
            "READ_WITH_WRITE": AttrWriteType.READ_WITH_WRITE,
        }.get(write_type_name, AttrWriteType.READ_WRITE)

    def read_dynamic_attr(self, attr):
        name = attr.get_name()
        with self.client_lock:
            value = self.sdo(name).raw
        self.debug_stream("Read value %s: %s", name, value)
        attr.set_value(value)

    def write_dynamic_attr(self, attr):
        name = attr.get_name()
        value = attr.get_write_value()
        with self.client_lock:
            self.sdo(name).raw = value
        self.debug_stream("Write value %s: %s", name, value)
        self.push_change_event(name, value)

    def sdo(self, name):
        indexName = self.dynamic_attribute_indices[name]
        if indexName.startswith("0x"): # hex index sdo
            if "#" in indexName:
                mainIndex, subIndex = indexName.split("#")
                mainIndexHex = int(mainIndex, 16)
                subIndexHex = int(subIndex, 16)
                return self.node.sdo[mainIndexHex][subIndexHex]
            else:
                return self.node.sdo[int(indexName, 16)]
        if indexName.isdigit():  # integer index sdo
            return self.node.sdo[int(indexName)]
        return self.node.sdo[indexName] # named sdo

    # canopen surfaces a lost node only as an sdo timeout: on socketcan the bus interface stays up
    # regardless of any node, there is no connection object that drops, and the node is not assumed
    # to be a heartbeat producer (the general case, and our counterpart is not one), so heartbeat /
    # node-guarding monitoring is not available. The node is therefore watched actively with a real
    # sdo read - a last resort, only because the library gives no signal of its own. Nothing has to
    # be reopened on recovery: as soon as the node answers its sdo again the driver returns to ON.
    def health_probe(self):
        if self.node is None or not self.dynamic_attribute_indices:
            return
        anyName = next(iter(self.dynamic_attribute_indices))
        with self.client_lock:
            self.sdo(anyName).raw

    def health_loop(self):
        wasHealthy = True
        while not self._health_stop.is_set():
            self._health_stop.wait(self.health_check_interval)
            if self._health_stop.is_set():
                break
            try:
                self.health_probe()
                if not wasHealthy:
                    self.info_stream("connection to the node recovered")
                    self.set_state(DevState.ON)
                    wasHealthy = True
            except Exception as e:
                if wasHealthy:
                    self.warn_stream("lost connection to the node: %s", str(e))
                    self.set_state(DevState.FAULT)
                    wasHealthy = False

    def start_health_monitor(self):
        self._health_stop = Event()
        self._health_thread = Thread(target=self.health_loop, daemon=True)
        self._health_thread.start()

    def delete_device(self):
        if self._health_stop is not None:
            self._health_stop.set()
        if self.network is not None:
            try:
                self.network.disconnect()
            except Exception:
                pass
            self.network = None
        self.node = None

    def init_device(self):
        self.set_state(DevState.INIT)
        self.get_device_properties(self.get_device_class())
        self.network = canopen.Network()
        try:
            self.network.connect(channel=self.network_channel, interface=self.network_interface,
                bitrate=self.network_bitrate)
        except Exception as e:
            self.error_stream("Failed to connect: %s", str(e))
            self.set_state(DevState.FAULT)
        self.info_stream("Adding node %s with EDS %s", self.node_id, self.eds_file)
        temp_eds_file = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.eds')
        temp_eds_file.write(self.eds_file)
        temp_eds_file.close()
        self.debug_stream("EDS content written to temporary file: %s", temp_eds_file.name)
        self.node = canopen.RemoteNode(int(self.node_id), temp_eds_file.name)
        # self.node = canopen.RemoteNode(self.node_id, self.eds_file) not allowed to load directly
        self.network.add_node(self.node)
        for entry in self.node.object_dictionary:
            obj = self.node.object_dictionary[entry]
            self.debug_stream("Object Index: %s, Name: %s", entry, obj.name)

        os.remove(temp_eds_file.name)
        if self.init_dynamic_attributes:
            try:
                attributes = json.loads(self.init_dynamic_attributes)
                for attr_data in attributes:
                    self.add_dynamic_attribute(attr_data["name"], attr_data["register"], 
                        attr_data.get("data_type", ""), attr_data.get("min_value", ""), attr_data.get("max_value", ""),
                        attr_data.get("unit", ""), attr_data.get("write_type", ""),
                        attr_data.get("min_alarm", ""), attr_data.get("max_alarm", ""),
                        attr_data.get("min_warning", ""), attr_data.get("max_warning", ""))
            except JSONDecodeError as e:
                self.error_stream("Failed to parse init_dynamic_attributes JSON: %s", str(e))

        if self.get_state() != DevState.FAULT:
            self.set_state(DevState.ON)
        self.start_health_monitor()

if __name__ == "__main__":
    deviceServerName = os.getenv("DEVICE_SERVER_NAME", "Canopen")
    run({deviceServerName: Canopen})
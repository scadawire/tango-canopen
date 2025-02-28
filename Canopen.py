import time
from tango import AttrQuality, AttrWriteType, DevState, Attr, CmdArgType, UserDefaultAttrProp
from tango.server import Device, attribute, command, DeviceMeta
from tango.server import class_property, device_property, run
import os
import json
from json import JSONDecodeError
import tempfile
import canopen

class Canopen(Device, metaclass=DeviceMeta):
    network = None
    node = None
    dynamic_attribute_indices = {}

    network_channel = device_property(dtype=str, default_value="can0")
    network_interface = device_property(dtype=str, default_value="socketcan")
    network_bitrate = device_property(dtype=int, default_value=0)
    eds_file = device_property(dtype=str, default_value="")
    node_id = device_property(dtype=int, default_value=1)
    init_dynamic_attributes = device_property(dtype=str, default_value="")

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
        self.dynamic_attribute_indices[name] = index
        print(f"Added dynamic attribute {index} {name}")

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
        value = self.sdo(name).raw
        self.debug_stream(f"Read value {name}: {value}")
        attr.set_value(value)

    def write_dynamic_attr(self, attr):
        name = attr.get_name()
        value = attr.get_write_value()        
        self.sdo(name).raw = value
        self.debug_stream(f"Write value {name}: {value}")

    def sdo(self, name):
        indexName = self.dynamic_attribute_indices[name]
        if indexName.startswith("0x"): # hex index sdo
            if "#" in indexName:
                mainIndex, subIndex = indexName.split("#")
                mainIndexHex = int(mainIndex, 16)
                subIndexHex = int(subIndex, 16)
                return self.node.sdo[mainIndex][subIndexHex]
            else:
                return self.node.sdo[int(indexName, 16)]
        if indexName.isdigit():  # integer index sdo
            return self.node.sdo[int(indexName)]
        return self.node.sdo[indexName] # named sdo

    def init_device(self):
        self.set_state(DevState.INIT)
        self.get_device_properties(self.get_device_class())
        self.network = canopen.Network()
        self.network.connect(channel=self.network_channel, interface=self.network_interface,
            bitrate=self.network_bitrate)
        self.info_stream(f"Adding node {self.node_id} with EDS {self.eds_file}")
        temp_eds_file = tempfile.NamedTemporaryFile(delete=False, mode='w', suffix='.eds')
        temp_eds_file.write(self.eds_file)
        temp_eds_file.close()
        self.info_stream(f"EDS content written to temporary file: {temp_eds_file.name}")
        self.node = canopen.RemoteNode(int(self.node_id), temp_eds_file.name)
        # self.node = canopen.RemoteNode(self.node_id, self.eds_file) not allowed to load directly
        self.network.add_node(self.node)
        for entry in self.node.object_dictionary:
            obj = self.node.object_dictionary[entry]
            print(f"Object Index: {entry}, Name: {obj.name}")

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
                raise e

        self.set_state(DevState.ON)

if __name__ == "__main__":
    deviceServerName = os.getenv("DEVICE_SERVER_NAME", "Canopen")
    run({deviceServerName: Canopen})
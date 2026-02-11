"""
Unit test for Canopen.py using virtual CAN bus simulation.

Uses python-can's virtual interface with canopen LocalNode (server) and
RemoteNode (client) on the same in-process virtual bus. SDO requests travel
through real CAN messaging — no mock SDO objects for integration tests.

Pure conversion tests and fix verification tests remain mock-based.

Tests: type/write-type conversion, SDO simulation (bool, int, float, double,
       string, record), SDO index resolution via real protocol, integration
       via Canopen class methods, and bug verification.

Usage:
    python test_canopen.py
"""

import struct
import sys

import canopen
import canopen.objectdictionary as od_mod
from canopen.objectdictionary import datatypes

from tango import CmdArgType, AttrWriteType

from Canopen import Canopen


# ===========================================================================
#  Mock objects -- for pure/unit tests (no CAN bus needed)
# ===========================================================================

class MockSdoEntry:
    def __init__(self):
        self.raw = None
        self._subs = {}

    def __getitem__(self, key):
        if key not in self._subs:
            self._subs[key] = MockSdoEntry()
        return self._subs[key]


class MockSdo:
    def __init__(self):
        self._entries = {}

    def __getitem__(self, key):
        if key not in self._entries:
            self._entries[key] = MockSdoEntry()
        return self._entries[key]


class MockNode:
    def __init__(self):
        self.sdo = MockSdo()


class MockTangoAttr:
    def __init__(self, name, write_value=None):
        self._name = name
        self._value = None
        self._write_value = write_value

    def get_name(self):
        return self._name

    def set_value(self, v):
        self._value = v

    def get_write_value(self):
        return self._write_value


# ===========================================================================
#  State carrier (mock-based, for pure/unit tests)
# ===========================================================================

class State:
    """Carries instance state; method lookups fall through to Canopen."""

    def __init__(self):
        self.network = None
        self.node = MockNode()
        self.dynamic_attribute_indices = {}

    def info_stream(self, msg): pass
    def debug_stream(self, msg): pass
    def warn_stream(self, msg): pass
    def error_stream(self, msg): pass

    def add_attribute(self, attr, r_meth=None, w_meth=None):
        pass

    def __getattr__(self, name):
        import functools
        attr = getattr(Canopen, name, None)
        if attr is not None and callable(attr):
            return functools.partial(attr, self)
        raise AttributeError(f"'State' has no attribute '{name}'")


# ===========================================================================
#  Virtual CAN bus setup
# ===========================================================================

def build_object_dictionary():
    """Build an OD with test variables at 0x2000-0x2005."""
    dictionary = canopen.ObjectDictionary()

    # 0x2000: Boolean
    v = od_mod.ODVariable("TestBool", 0x2000)
    v.data_type = datatypes.BOOLEAN
    dictionary.add_object(v)

    # 0x2001: Integer32
    v = od_mod.ODVariable("TestInt32", 0x2001)
    v.data_type = datatypes.INTEGER32
    dictionary.add_object(v)

    # 0x2002: Float (REAL32)
    v = od_mod.ODVariable("TestFloat", 0x2002)
    v.data_type = datatypes.REAL32
    dictionary.add_object(v)

    # 0x2003: Double (REAL64)
    v = od_mod.ODVariable("TestDouble", 0x2003)
    v.data_type = datatypes.REAL64
    dictionary.add_object(v)

    # 0x2004: String (VISIBLE_STRING)
    v = od_mod.ODVariable("TestString", 0x2004)
    v.data_type = datatypes.VISIBLE_STRING
    dictionary.add_object(v)

    # 0x2005: Record with two INTEGER16 sub-indices
    rec = od_mod.ODRecord("TestRecord", 0x2005)
    sub0 = od_mod.ODVariable("NumberOfEntries", 0x2005, 0)
    sub0.data_type = datatypes.UNSIGNED8
    sub0.default = 2
    rec.add_member(sub0)
    sub1 = od_mod.ODVariable("Value1", 0x2005, 1)
    sub1.data_type = datatypes.INTEGER16
    rec.add_member(sub1)
    sub2 = od_mod.ODVariable("Value2", 0x2005, 2)
    sub2.data_type = datatypes.INTEGER16
    rec.add_member(sub2)
    dictionary.add_object(rec)

    return dictionary


def create_virtual_bus():
    """Create server (LocalNode) and client (RemoteNode) on a virtual CAN bus.

    Returns (server_network, client_network, local_node, remote_node, od).
    """
    dictionary = build_object_dictionary()

    server_network = canopen.Network()
    server_network.connect(channel="test_canopen", interface="virtual")
    local_node = server_network.create_node(1, dictionary)

    client_network = canopen.Network()
    client_network.connect(channel="test_canopen", interface="virtual")
    remote_node = client_network.add_node(1, dictionary)

    return server_network, client_network, local_node, remote_node, dictionary


# ===========================================================================
#  Test helpers
# ===========================================================================

passed = 0
failed = 0
errors = []


def assert_equal(test_name, actual, expected, tolerance=None):
    global passed, failed
    if tolerance is not None:
        ok = abs(actual - expected) <= tolerance
    else:
        ok = (actual == expected)

    if ok:
        passed += 1
        print(f"  PASS  {test_name}")
    else:
        failed += 1
        msg = f"  FAIL  {test_name}: expected {expected!r}, got {actual!r}"
        print(msg)
        errors.append(msg)


def assert_true(test_name, value):
    assert_equal(test_name, value, True)


def assert_false(test_name, value):
    assert_equal(test_name, value, False)


def assert_raises(test_name, exc_type, fn, *args):
    global passed, failed
    try:
        fn(*args)
        failed += 1
        msg = f"  FAIL  {test_name}: expected {exc_type.__name__}, got none"
        print(msg)
        errors.append(msg)
    except exc_type:
        passed += 1
        print(f"  PASS  {test_name}")
    except Exception as e:
        failed += 1
        msg = f"  FAIL  {test_name}: expected {exc_type.__name__}, got {type(e).__name__}: {e}"
        print(msg)
        errors.append(msg)


# ===========================================================================
#  Pure conversion tests (mock-based, no CAN bus)
# ===========================================================================

def test_string_value_to_var_type():
    print("\n-- stringValueToVarType --")
    s = State()

    for name, expected in [
        ("DevBoolean", CmdArgType.DevBoolean),
        ("DevLong", CmdArgType.DevLong),
        ("DevDouble", CmdArgType.DevDouble),
        ("DevFloat", CmdArgType.DevFloat),
        ("DevString", CmdArgType.DevString),
    ]:
        got = Canopen.stringValueToVarType(s, name)
        assert_equal(f"varType '{name}'", got, expected)

    assert_equal("varType '' default", Canopen.stringValueToVarType(s, ""), CmdArgType.DevString)
    assert_equal("varType unknown default", Canopen.stringValueToVarType(s, "DevInvalid"), CmdArgType.DevString)


def test_string_value_to_write_type():
    print("\n-- stringValueToWriteType --")
    s = State()

    for name, expected in [
        ("READ", AttrWriteType.READ),
        ("WRITE", AttrWriteType.WRITE),
        ("READ_WRITE", AttrWriteType.READ_WRITE),
        ("READ_WITH_WRITE", AttrWriteType.READ_WITH_WRITE),
    ]:
        got = Canopen.stringValueToWriteType(s, name)
        assert_equal(f"writeType '{name}'", got, expected)

    assert_equal("writeType '' default", Canopen.stringValueToWriteType(s, ""), AttrWriteType.READ_WRITE)
    assert_equal("writeType unknown default", Canopen.stringValueToWriteType(s, "BOGUS"), AttrWriteType.READ_WRITE)


# ===========================================================================
#  SDO index resolution tests (mock-based)
# ===========================================================================

def test_sdo_hex_index():
    print("\n-- SDO hex index --")
    s = State()
    s.dynamic_attribute_indices["temp"] = "0x1234"
    s.node.sdo[0x1234].raw = 42
    sdo_var = Canopen.sdo(s, "temp")
    assert_equal("hex index 0x1234", sdo_var.raw, 42)


def test_sdo_hex_with_subindex():
    print("\n-- SDO hex with sub-index --")
    s = State()
    s.dynamic_attribute_indices["sensor"] = "0x2000#0x01"
    s.node.sdo[0x2000][0x01].raw = 99
    sdo_var = Canopen.sdo(s, "sensor")
    assert_equal("hex+sub 0x2000#0x01", sdo_var.raw, 99)


def test_sdo_integer_index():
    print("\n-- SDO integer index --")
    s = State()
    s.dynamic_attribute_indices["counter"] = "1234"
    s.node.sdo[1234].raw = 77
    sdo_var = Canopen.sdo(s, "counter")
    assert_equal("integer index 1234", sdo_var.raw, 77)


def test_sdo_named_index():
    print("\n-- SDO named index --")
    s = State()
    s.dynamic_attribute_indices["temp_attr"] = "Temperature"
    s.node.sdo["Temperature"].raw = 23.5
    sdo_var = Canopen.sdo(s, "temp_attr")
    assert_equal("named index 'Temperature'", sdo_var.raw, 23.5)


def test_sdo_hex_subindex_variations():
    print("\n-- SDO hex sub-index variations --")
    s = State()
    s.dynamic_attribute_indices["a"] = "0x3000#0x00"
    s.node.sdo[0x3000][0x00].raw = 10
    s.dynamic_attribute_indices["b"] = "0x3000#0x02"
    s.node.sdo[0x3000][0x02].raw = 20

    global passed, failed
    try:
        Canopen.sdo(s, "a")
        Canopen.sdo(s, "b")
        passed += 1
        print("  PASS  hex sub-index variations: no crash")
    except Exception as e:
        failed += 1
        msg = f"  FAIL  hex sub-index variations: {e}"
        print(msg)
        errors.append(msg)


# ===========================================================================
#  Mock-based read/write tests
# ===========================================================================

def test_read_write_numeric():
    print("\n-- read/write numeric via SDO (mock) --")
    s = State()
    s.dynamic_attribute_indices["val_int"] = "100"

    attr_w = MockTangoAttr("val_int", write_value=42)
    Canopen.write_dynamic_attr(s, attr_w)
    attr_r = MockTangoAttr("val_int")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("read-back int 42", attr_r._value, 42)

    attr_w = MockTangoAttr("val_int", write_value=3.14)
    Canopen.write_dynamic_attr(s, attr_w)
    attr_r = MockTangoAttr("val_int")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("read-back float 3.14", attr_r._value, 3.14)

    attr_w = MockTangoAttr("val_int", write_value=-100)
    Canopen.write_dynamic_attr(s, attr_w)
    attr_r = MockTangoAttr("val_int")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("read-back neg -100", attr_r._value, -100)


def test_read_write_hex():
    print("\n-- read/write hex index (mock) --")
    s = State()
    s.dynamic_attribute_indices["hex_var"] = "0x2001"

    attr_w = MockTangoAttr("hex_var", write_value=255)
    Canopen.write_dynamic_attr(s, attr_w)
    attr_r = MockTangoAttr("hex_var")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("hex 0x2001 read-back", attr_r._value, 255)


def test_read_write_named():
    print("\n-- read/write named index (mock) --")
    s = State()
    s.dynamic_attribute_indices["named_var"] = "Pressure"

    for val in [0, 101325, -50, 1.013e5]:
        attr_w = MockTangoAttr("named_var", write_value=val)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("named_var")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal(f"named {val}", attr_r._value, val)


def test_read_write_string():
    print("\n-- read/write string values (mock) --")
    s = State()
    s.dynamic_attribute_indices["str_var"] = "DeviceName"

    for val in ["Hello", "CAN-Node-1", "", "A" * 100]:
        attr_w = MockTangoAttr("str_var", write_value=val)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("str_var")
        Canopen.read_dynamic_attr(s, attr_r)
        label = f"string '{val[:20]}..'" if len(val) > 20 else f"string '{val}'"
        assert_equal(label, attr_r._value, val)


def test_read_write_bool():
    print("\n-- read/write boolean values (mock) --")
    s = State()
    s.dynamic_attribute_indices["flag"] = "500"

    for val in [True, False, True, False]:
        attr_w = MockTangoAttr("flag", write_value=val)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("flag")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal(f"bool {val}", attr_r._value, val)


def test_overwrite():
    print("\n-- overwrite values (mock) --")
    s = State()
    s.dynamic_attribute_indices["ow"] = "600"

    for val in [1, 2, 3]:
        attr_w = MockTangoAttr("ow", write_value=val)
        Canopen.write_dynamic_attr(s, attr_w)

    attr_r = MockTangoAttr("ow")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("overwrite final", attr_r._value, 3)


def test_multiple_attributes():
    print("\n-- multiple attributes (mock) --")
    s = State()
    names_indices = [
        ("attr_a", "100"),
        ("attr_b", "0x200"),
        ("attr_c", "Speed"),
    ]
    values = [42, 255, 3.14]

    for name, idx in names_indices:
        s.dynamic_attribute_indices[name] = idx

    for (name, _), val in zip(names_indices, values):
        attr_w = MockTangoAttr(name, write_value=val)
        Canopen.write_dynamic_attr(s, attr_w)

    for (name, _), val in zip(names_indices, values):
        attr_r = MockTangoAttr(name)
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal(f"multi {name}", attr_r._value, val)


# ===========================================================================
#  add_dynamic_attribute tests (mock-based)
# ===========================================================================

def test_add_dynamic_attribute():
    print("\n-- add_dynamic_attribute (mock) --")
    s = State()

    Canopen.add_dynamic_attribute(s, "motor_speed", "0x6041")
    assert_true("index registered", "motor_speed" in s.dynamic_attribute_indices)
    assert_equal("index value", s.dynamic_attribute_indices["motor_speed"], "0x6041")

    attr_w = MockTangoAttr("motor_speed", write_value=1500)
    Canopen.write_dynamic_attr(s, attr_w)
    attr_r = MockTangoAttr("motor_speed")
    Canopen.read_dynamic_attr(s, attr_r)
    assert_equal("add_dynamic_attribute read-back", attr_r._value, 1500)

    Canopen.add_dynamic_attribute(s, "temp", "0x6042",
        variable_type_name="DevFloat", write_type_name="READ_WRITE",
        unit="C")
    assert_true("typed attr registered", "temp" in s.dynamic_attribute_indices)

    count_before = len(s.dynamic_attribute_indices)
    Canopen.add_dynamic_attribute(s, "skip_me", "")
    assert_equal("empty index no-op", len(s.dynamic_attribute_indices), count_before)


def test_add_dynamic_attribute_with_limits():
    print("\n-- add_dynamic_attribute with limits (mock) --")
    s = State()

    Canopen.add_dynamic_attribute(s, "limited", "700",
        variable_type_name="DevFloat",
        min_value="0", max_value="100",
        min_alarm="5", max_alarm="95",
        min_warning="10", max_warning="90")
    assert_true("limited attr registered", "limited" in s.dynamic_attribute_indices)

    Canopen.add_dynamic_attribute(s, "eq_limits", "701",
        variable_type_name="DevFloat",
        min_value="50", max_value="50")
    assert_true("equal limits attr registered", "eq_limits" in s.dynamic_attribute_indices)


# ===========================================================================
#  SDO simulation tests -- round-trip through virtual CAN bus
# ===========================================================================

def test_sdo_sim_boolean():
    """Boolean write/read through real SDO protocol on virtual CAN bus."""
    print("\n-- SDO sim: boolean --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        remote_node.sdo[0x2000].raw = True
        assert_equal("bool write True", remote_node.sdo[0x2000].raw, True)

        remote_node.sdo[0x2000].raw = False
        assert_equal("bool write False", remote_node.sdo[0x2000].raw, False)

        remote_node.sdo[0x2000].raw = True
        assert_equal("bool toggle back True", remote_node.sdo[0x2000].raw, True)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_integer():
    """Integer32 write/read through real SDO protocol."""
    print("\n-- SDO sim: integer32 --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        for val in [0, 1, -1, 42, 2147483647, -2147483648, 255, -100]:
            remote_node.sdo[0x2001].raw = val
            got = remote_node.sdo[0x2001].raw
            assert_equal(f"int32 {val}", got, val)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_float():
    """REAL32 (float) write/read through real SDO protocol."""
    print("\n-- SDO sim: float (REAL32) --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        for val in [0.0, 1.5, -3.14, 1.0e10, 1.0e-10]:
            remote_node.sdo[0x2002].raw = val
            got = remote_node.sdo[0x2002].raw
            assert_equal(f"float {val}", got, val, tolerance=abs(val * 1e-6) + 1e-30)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_double():
    """REAL64 (double) write/read through real SDO protocol."""
    print("\n-- SDO sim: double (REAL64) --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        for val in [0.0, 3.141592653589793, -2.718281828459045, 1.0e100, 1.0e-100]:
            remote_node.sdo[0x2003].raw = val
            got = remote_node.sdo[0x2003].raw
            assert_equal(f"double {val}", got, val, tolerance=abs(val * 1e-15) + 1e-300)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_string():
    """VISIBLE_STRING write/read through real SDO protocol."""
    print("\n-- SDO sim: string --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        for val in ["Hello", "CAN-Node-1", "A" * 50, "test123"]:
            remote_node.sdo[0x2004].raw = val
            got = remote_node.sdo[0x2004].raw
            label = f"string '{val[:20]}'" if len(val) > 20 else f"string '{val}'"
            assert_equal(label, got, val)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_record_subindex():
    """Record sub-index write/read through real SDO protocol."""
    print("\n-- SDO sim: record sub-index --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        remote_node.sdo[0x2005][1].raw = 1234
        remote_node.sdo[0x2005][2].raw = -5678

        got1 = remote_node.sdo[0x2005][1].raw
        got2 = remote_node.sdo[0x2005][2].raw
        assert_equal("record sub1 = 1234", got1, 1234)
        assert_equal("record sub2 = -5678", got2, -5678)

        # overwrite
        remote_node.sdo[0x2005][1].raw = 99
        assert_equal("record sub1 overwrite", remote_node.sdo[0x2005][1].raw, 99)
        # sub2 unchanged
        assert_equal("record sub2 unchanged", remote_node.sdo[0x2005][2].raw, -5678)
    finally:
        client_net.disconnect()
        server_net.disconnect()


# ===========================================================================
#  SDO index resolution with simulation
# ===========================================================================

def test_sdo_sim_hex_index():
    """Verify hex index resolution works through real CAN protocol."""
    print("\n-- SDO sim: hex index resolution --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node
        s.dynamic_attribute_indices["test_int"] = "0x2001"

        # Write via Canopen.sdo(), read back
        Canopen.sdo(s, "test_int").raw = 12345
        got = Canopen.sdo(s, "test_int").raw
        assert_equal("sim hex 0x2001 round-trip", got, 12345)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_hex_subindex():
    """Verify hex+sub index resolution works through real CAN protocol."""
    print("\n-- SDO sim: hex+sub index resolution --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node
        s.dynamic_attribute_indices["rec_val"] = "0x2005#0x01"

        Canopen.sdo(s, "rec_val").raw = 777
        got = Canopen.sdo(s, "rec_val").raw
        assert_equal("sim hex+sub 0x2005#0x01 round-trip", got, 777)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_integer_index():
    """Verify integer string index works through real CAN protocol."""
    print("\n-- SDO sim: integer index resolution --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node
        # 0x2001 == 8193 decimal
        s.dynamic_attribute_indices["dec_var"] = "8193"

        Canopen.sdo(s, "dec_var").raw = -42
        got = Canopen.sdo(s, "dec_var").raw
        assert_equal("sim integer index 8193 round-trip", got, -42)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sdo_sim_named_index():
    """Verify named index works through real CAN protocol."""
    print("\n-- SDO sim: named index resolution --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node
        s.dynamic_attribute_indices["my_float"] = "TestFloat"

        Canopen.sdo(s, "my_float").raw = 2.5
        got = Canopen.sdo(s, "my_float").raw
        assert_equal("sim named 'TestFloat' round-trip", got, 2.5, tolerance=1e-6)
    finally:
        client_net.disconnect()
        server_net.disconnect()


# ===========================================================================
#  Integration via Canopen class methods with real CAN bus
# ===========================================================================

def test_sim_write_read_dynamic_attr():
    """write_dynamic_attr / read_dynamic_attr through real SDO protocol."""
    print("\n-- SDO sim: write_dynamic_attr / read_dynamic_attr --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node

        # Integer
        s.dynamic_attribute_indices["sim_int"] = "0x2001"
        attr_w = MockTangoAttr("sim_int", write_value=9999)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_int")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read int 9999", attr_r._value, 9999)

        # Boolean
        s.dynamic_attribute_indices["sim_bool"] = "0x2000"
        attr_w = MockTangoAttr("sim_bool", write_value=True)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_bool")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read bool True", attr_r._value, True)

        # Float
        s.dynamic_attribute_indices["sim_float"] = "0x2002"
        attr_w = MockTangoAttr("sim_float", write_value=3.14)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_float")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read float 3.14", attr_r._value, 3.14, tolerance=1e-5)

        # String
        s.dynamic_attribute_indices["sim_str"] = "0x2004"
        attr_w = MockTangoAttr("sim_str", write_value="canopen-test")
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_str")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read string", attr_r._value, "canopen-test")

        # Record sub-index
        s.dynamic_attribute_indices["sim_rec"] = "0x2005#0x02"
        attr_w = MockTangoAttr("sim_rec", write_value=321)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_rec")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read record sub2", attr_r._value, 321)

        # Double
        s.dynamic_attribute_indices["sim_dbl"] = "0x2003"
        attr_w = MockTangoAttr("sim_dbl", write_value=2.718281828459045)
        Canopen.write_dynamic_attr(s, attr_w)
        attr_r = MockTangoAttr("sim_dbl")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim write/read double", attr_r._value, 2.718281828459045, tolerance=1e-14)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sim_overwrite_values():
    """Overwrite values through real SDO protocol."""
    print("\n-- SDO sim: overwrite values --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node
        s.dynamic_attribute_indices["ow"] = "0x2001"

        for val in [10, 20, 30, 40, 50]:
            attr_w = MockTangoAttr("ow", write_value=val)
            Canopen.write_dynamic_attr(s, attr_w)

        attr_r = MockTangoAttr("ow")
        Canopen.read_dynamic_attr(s, attr_r)
        assert_equal("sim overwrite final = 50", attr_r._value, 50)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sim_multiple_attrs():
    """Multiple attributes on the same virtual bus."""
    print("\n-- SDO sim: multiple attributes --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        s = State()
        s.node = remote_node

        attrs = [
            ("a_bool", "0x2000", True),
            ("a_int", "0x2001", -12345),
            ("a_float", "0x2002", 1.5),
            ("a_str", "0x2004", "multi-test"),
        ]

        for name, idx, val in attrs:
            s.dynamic_attribute_indices[name] = idx
            attr_w = MockTangoAttr(name, write_value=val)
            Canopen.write_dynamic_attr(s, attr_w)

        for name, idx, val in attrs:
            attr_r = MockTangoAttr(name)
            Canopen.read_dynamic_attr(s, attr_r)
            tol = 1e-5 if isinstance(val, float) else None
            assert_equal(f"sim multi {name} = {val!r}", attr_r._value, val, tolerance=tol)
    finally:
        client_net.disconnect()
        server_net.disconnect()


def test_sim_data_store_persistence():
    """Verify LocalNode data_store persists values between accesses."""
    print("\n-- SDO sim: data_store persistence --")
    server_net, client_net, local_node, remote_node, od = create_virtual_bus()
    try:
        # Write via SDO
        remote_node.sdo[0x2001].raw = 42

        # Verify data_store has the raw bytes
        stored = local_node.data_store.get(0x2001, {}).get(0, None)
        assert_true("data_store has 0x2001", stored is not None)
        assert_equal("data_store value", struct.unpack("<i", stored)[0], 42)

        # Read back via SDO still works
        assert_equal("read after store check", remote_node.sdo[0x2001].raw, 42)
    finally:
        client_net.disconnect()
        server_net.disconnect()


# ===========================================================================
#  Bug verification / fix tests
# ===========================================================================

def test_fix_sdo_hex_subindex():
    """Line 88 (fixed): sdo() with hex+sub should use int mainIndexHex."""
    print("\n-- FIX: sdo hex+sub uses int key (line 88) --")
    s = State()
    s.dynamic_attribute_indices["x"] = "0x2000#0x01"
    s.node.sdo[0x2000][0x01].raw = "correct"
    sdo_var = Canopen.sdo(s, "x")
    assert_equal("sdo hex+sub resolves to int key", sdo_var.raw, "correct")


def test_fix_json_decode_logged():
    """Line 123-124 (fixed): JSONDecodeError should be logged, not re-raised."""
    print("\n-- FIX: JSONDecodeError logged --")
    import inspect
    source = inspect.getsource(Canopen.init_device)
    has_reraise = "raise e" in source
    has_error_log = "error_stream" in source

    global passed, failed
    if has_reraise:
        failed += 1
        msg = "  FAIL  JSONDecodeError: still re-raises"
        print(msg)
        errors.append(msg)
    else:
        passed += 1
        print("  PASS  JSONDecodeError: does not re-raise")

    if has_error_log:
        passed += 1
        print("  PASS  JSONDecodeError: logs with error_stream")
    else:
        failed += 1
        msg = "  FAIL  JSONDecodeError: does not log error"
        print(msg)
        errors.append(msg)


def test_fix_delete_device():
    """delete_device should disconnect network and clean up."""
    print("\n-- FIX: delete_device --")
    has_own_delete = "delete_device" in Canopen.__dict__
    assert_true("delete_device overridden", has_own_delete)

    s = State()

    class MockNetwork:
        disconnected = False
        def disconnect(self):
            MockNetwork.disconnected = True

    s.network = MockNetwork()
    s.node = MockNode()
    Canopen.delete_device(s)
    assert_true("network.disconnect called", MockNetwork.disconnected)
    assert_equal("network is None after delete", s.network, None)
    assert_equal("node is None after delete", s.node, None)

    Canopen.delete_device(s)
    assert_equal("network still None after double delete", s.network, None)


def test_fix_connect_error_handling():
    """init_device should wrap network.connect() in try/except."""
    print("\n-- FIX: connect error handling --")
    import inspect
    source = inspect.getsource(Canopen.init_device)
    lines = source.split("\n")
    connect_line = None
    for i, line in enumerate(lines):
        if ".connect(" in line and "network" in line:
            preceding = "\n".join(lines[:i])
            try_count = preceding.count("try:")
            except_count = preceding.count("except")
            if try_count > except_count:
                connect_line = i
                break

    global passed, failed
    if connect_line is not None:
        passed += 1
        print("  PASS  connect() wrapped in try/except")
    else:
        failed += 1
        msg = "  FAIL  connect(): not wrapped in try/except"
        print(msg)
        errors.append(msg)


def test_fix_set_on_checks_fault():
    """init_device should only set ON if not in FAULT state."""
    print("\n-- FIX: set_state(ON) checks FAULT --")
    import inspect
    source = inspect.getsource(Canopen.init_device)
    has_fault_check = "FAULT" in source and "get_state" in source

    global passed, failed
    if has_fault_check:
        passed += 1
        print("  PASS  init_device checks FAULT before setting ON")
    else:
        failed += 1
        msg = "  FAIL  init_device: sets ON without checking FAULT state"
        print(msg)
        errors.append(msg)


# ===========================================================================
#  Main
# ===========================================================================

def main():
    global passed, failed

    # -- pure conversion --
    test_string_value_to_var_type()
    test_string_value_to_write_type()

    # -- SDO index resolution (mock) --
    test_sdo_hex_index()
    test_sdo_hex_with_subindex()
    test_sdo_integer_index()
    test_sdo_named_index()
    test_sdo_hex_subindex_variations()

    # -- mock read/write --
    test_read_write_numeric()
    test_read_write_hex()
    test_read_write_named()
    test_read_write_string()
    test_read_write_bool()
    test_overwrite()
    test_multiple_attributes()

    # -- add_dynamic_attribute --
    test_add_dynamic_attribute()
    test_add_dynamic_attribute_with_limits()

    # -- SDO simulation (virtual CAN bus) --
    test_sdo_sim_boolean()
    test_sdo_sim_integer()
    test_sdo_sim_float()
    test_sdo_sim_double()
    test_sdo_sim_string()
    test_sdo_sim_record_subindex()

    # -- SDO index resolution with simulation --
    test_sdo_sim_hex_index()
    test_sdo_sim_hex_subindex()
    test_sdo_sim_integer_index()
    test_sdo_sim_named_index()

    # -- integration via Canopen class methods --
    test_sim_write_read_dynamic_attr()
    test_sim_overwrite_values()
    test_sim_multiple_attrs()
    test_sim_data_store_persistence()

    # -- fix verification --
    test_fix_sdo_hex_subindex()
    test_fix_json_decode_logged()
    test_fix_delete_device()
    test_fix_connect_error_handling()
    test_fix_set_on_checks_fault()

    # -- summary --
    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    if errors:
        print("\n  Failures:")
        for e in errors:
            print(f"    {e}")
    print(f"{'=' * 50}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

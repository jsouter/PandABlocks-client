import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    Generic,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
    overload,
)

from ._exchange import Exchange, ExchangeGenerator
from .responses import (
    BitMuxFieldInfo,
    BitOutFieldInfo,
    BlockInfo,
    Changes,
    EnumFieldInfo,
    ExtOutBitsFieldInfo,
    ExtOutFieldInfo,
    FieldInfo,
    PosMuxFieldInfo,
    PosOutFieldInfo,
    ScalarFieldInfo,
    SubtypeTimeFieldInfo,
    TimeFieldInfo,
    UintFieldInfo,
)

# Define the public API of this module
__all__ = [
    "Command",
    "CommandException",
    "Raw",
    "Get",
    "Put",
    "Arm",
    "Disarm",
    "GetBlockInfo",
    "GetFieldInfo",
    "GetPcapBitsLabels",
    "ChangeGroup",
    "GetChanges",
    "GetState",
    "SetState",
]


T = TypeVar("T")
T2 = TypeVar("T2")
T3 = TypeVar("T3")
T4 = TypeVar("T4")


# Checks whether the server will interpret cmd as a table command: search for
# first of '?', '=', '<', if '<' found first then it's a multiline command.
MULTILINE_COMMAND = re.compile(r"^[^?=]*<")


def is_multiline_command(cmd: str):
    return MULTILINE_COMMAND.match(cmd) is not None


@dataclass
class Command(Generic[T]):
    """Abstract baseclass for all ControlConnection commands to be inherited from"""

    def execute(self) -> ExchangeGenerator[T]:
        # A generator that sends lines to the PandA, gets lines back, and returns a
        # response
        raise NotImplementedError(self)


class CommandException(Exception):
    """Raised if a `Command` receives a mal-formed response"""


# `execute_commands()` actually returns a list with length equal to the number
# of tasks passed; however, Tuple is used similar to the annotation for
# zip() because typing does not support variadic type variables.  See
# typeshed PR #1550 for discussion.
@overload
def _execute_commands(c1: Command[T]) -> ExchangeGenerator[Tuple[T]]:
    ...


@overload
def _execute_commands(
    c1: Command[T], c2: Command[T2]
) -> ExchangeGenerator[Tuple[T, T2]]:
    ...


@overload
def _execute_commands(
    c1: Command[T], c2: Command[T2], c3: Command[T3]
) -> ExchangeGenerator[Tuple[T, T2, T3]]:
    ...


@overload
def _execute_commands(
    c1: Command[T], c2: Command[T2], c3: Command[T3], c4: Command[T4]
) -> ExchangeGenerator[Tuple[T, T2, T3, T4]]:
    ...


@overload
def _execute_commands(*commands: Command[Any]) -> ExchangeGenerator[Tuple[Any, ...]]:
    ...


def _execute_commands(*commands):
    """Call the `Command.execute` method on each of the commands to produce
    some `Exchange` generators, which are yielded back to the connection,
    then zip together the responses to those exchanges into a tuple"""
    # If we add type annotations to this function then mypy complains:
    # Overloaded function implementation does not accept all possible arguments
    # As we want to type check this, we put the logic in _zip_with_return
    ret = yield from _zip_with_return([command.execute() for command in commands])
    return ret


def _zip_with_return(
    generators: List[ExchangeGenerator[Any]],
) -> ExchangeGenerator[Tuple[Any, ...]]:
    # Sentinel to show what generators are not yet exhausted
    pending = object()
    returns = [pending] * len(generators)
    while True:
        yields: List[Exchange] = []
        for i, gen in enumerate(generators):
            # If we haven't exhausted the generator
            if returns[i] is pending:
                try:
                    # Get the exchanges that it wants to fill in
                    exchanges = next(gen)
                except StopIteration as e:
                    # Generator is exhausted, store its return value
                    returns[i] = e.value
                else:
                    # Add the exchanges to the list
                    if isinstance(exchanges, list):
                        yields += exchanges
                    else:
                        yields.append(exchanges)
        if yields:
            # There were some Exchanges yielded, so yield them all up
            # for the Connection to fill in
            yield yields
        else:
            # All the generators are exhausted, so return the tuple of all
            # their return values
            return tuple(returns)


@dataclass
class Raw(Command[List[str]]):
    """Send a raw command

    Args:
        inp: The input lines to send

    For example::

        Raw(["PCAP.ACTIVE?"]) -> ["OK =1"]
        Raw(["SEQ1.TABLE>", "1", "1", "0", "0", ""]) -> ["OK"]
        Raw(["SEQ1.TABLE?"]) -> ["!1", "!1", "!0", "!0", "."])
    """

    inp: List[str]

    def execute(self) -> ExchangeGenerator[List[str]]:
        ex = Exchange(self.inp)
        yield ex
        return ex.received


@dataclass
class Get(Command[Union[str, List[str]]]):
    """Get the value of a field or star command.

    Args:
        field: The field, attribute, or star command to get

    For example::

        Get("PCAP.ACTIVE?") -> "1"
        Get("SEQ1.TABLE?") -> ["1048576", "0", "1000", "1000"]
        Get("*IDN") -> "PandA 1.1..."
    """

    field: str

    def execute(self) -> ExchangeGenerator[Union[str, List[str]]]:
        ex = Exchange(f"{self.field}?")
        yield ex
        if ex.is_multiline:
            return ex.multiline
        else:
            # We got OK =value
            line = ex.line
            assert line.startswith("OK =")
            return line[4:]


@dataclass
class Put(Command[None]):
    """Put the value of a field.

    Args:
        field: The field, attribute, or star command to put
        value: The value, possibly multiline, to put

    For example::

        Put("PCAP.TRIG", "PULSE1.OUT")
        Put("SEQ1.TABLE", ["1048576", "0", "1000", "1000"])
    """

    field: str
    value: Union[str, List[str]] = ""

    def execute(self) -> ExchangeGenerator[None]:
        if isinstance(self.value, list):
            # Multiline table with blank line to terminate
            ex = Exchange([f"{self.field}<"] + self.value + [""])
        else:
            ex = Exchange(f"{self.field}={self.value}")
        yield ex
        assert ex.line == "OK"


class Arm(Command[None]):
    """Arm PCAP for an acquisition by sending ``*PCAP.ARM=``"""

    def execute(self) -> ExchangeGenerator[None]:
        ex = Exchange("*PCAP.ARM=")
        yield ex
        assert ex.line == "OK"


class Disarm(Command[None]):
    """Disarm PCAP, stopping acquisition by sending ``*PCAP.DISARM=``"""

    def execute(self) -> ExchangeGenerator[None]:
        ex = Exchange("*PCAP.DISARM=")
        yield ex
        assert ex.line == "OK"


@dataclass
class GetBlockInfo(Command[Dict[str, BlockInfo]]):
    """Get the name, number, and description of each block type
    in a dictionary, alphabetically ordered

    Args:
        skip_description: If `True`, prevents retrieving the description
            for each Block. This will reduce network calls.

    For example::

         GetBlockInfo() ->
             {
                 "LUT": BlockInfo(number=8, description="Lookup table"),
                 "PCAP": BlockInfo(number=1, description="Position capture control"),
                 ...
             }
    """

    skip_description: bool = False

    def execute(self) -> ExchangeGenerator[Dict[str, BlockInfo]]:
        ex = Exchange("*BLOCKS?")
        yield ex

        blocks_list, commands = [], []
        for line in ex.multiline:
            block, num = line.split()
            blocks_list.append((block, int(num)))
            commands.append(Get(f"*DESC.{block}"))

        if self.skip_description:
            # Must use tuple() to match type returned by _execute_commands
            description_values = tuple(None for _ in commands)
        else:
            description_values = yield from _execute_commands(*commands)

        block_infos = {
            block: BlockInfo(number=num, description=desc)
            for (block, num), desc in sorted(zip(blocks_list, description_values))
        }

        return block_infos


# TODO: docstring
@dataclass
class _FieldCommandMapping:
    command: Get
    field_info: FieldInfo
    attribute: str
    type_func: Callable


@dataclass
class GetFieldInfo(Command[Dict[str, FieldInfo]]):
    """Get the fields of a block, returning a `FieldInfo` (or appropriate subclass) for
    each one, ordered to match the definition order in the PandA
    TODO: Update this!

    Args:
        block: The name of the block type
        extended_metadata: If `True`, retrieves detailed metadata about a field and
            all of its attributes. This will cause an additional network round trip.
            If `False` only the field names and types will be returned. Default `True`.

    For example::

        GetFieldInfo("LUT") -> {
            "INPA":
                BitMuxFieldInfo(type='bit_mux',
                                subtype=None,
                                description='Input A',
                                max_delay=5
                                label=['TTLIN1.VAL', 'TTLIN2.VAL', ...]),
            ...}
    """

    block: str
    extended_metadata: bool = True

    def _commands_param_uint(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = UintFieldInfo(field_type, field_subtype)

        (max,) = yield (field_info, [Get(f"{self.block}1.{field_name}.MAX")])

        assert isinstance(max, str)
        field_info.max = int(max)
        return field_name, field_info

    def _commands_scalar(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = ScalarFieldInfo(field_type, field_subtype)

        units, scale, offset = yield (
            field_info,
            [
                Get(f"{self.block}.{field_name}.UNITS"),
                Get(f"{self.block}.{field_name}.SCALE"),
                Get(f"{self.block}.{field_name}.OFFSET"),
            ],
        )
        assert isinstance(scale, str)
        assert isinstance(offset, str)
        field_info.units = str(units)
        field_info.scale = float(scale)
        field_info.offset = int(offset)

        return field_name, field_info

    def _commands_subtype_time(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = SubtypeTimeFieldInfo(field_type, field_subtype)

        (units,) = yield (
            field_info,
            [
                Get(f"*ENUMS.{self.block}.{field_name}.UNITS"),
            ],
        )
        field_info.units_labels = list(units)

        return field_name, field_info

    def _commands_enum(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = EnumFieldInfo(field_type, field_subtype)

        (labels,) = yield (
            field_info,
            [
                Get(f"*ENUMS.{self.block}.{field_name}"),
            ],
        )
        field_info.labels = list(labels)

        return field_name, field_info

    def _commands_time(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = TimeFieldInfo(field_type, field_subtype)

        units, min = yield (
            field_info,
            [
                Get(f"*ENUMS.{self.block}.{field_name}.UNITS"),
                Get(f"{self.block}1.{field_name}.MIN"),
            ],
        )
        assert isinstance(min, str)
        field_info.units_labels = list(units)
        field_info.min = float(min)

        return field_name, field_info

    def _commands_bit_out(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = BitOutFieldInfo(field_type, field_subtype)

        capture_word, offset = yield (
            field_info,
            [
                Get(f"{self.block}1.{field_name}.CAPTURE_WORD"),
                Get(f"{self.block}1.{field_name}.OFFSET"),
            ],
        )
        assert isinstance(offset, str)
        field_info.capture_word = str(capture_word)
        field_info.offset = int(offset)

        return field_name, field_info

    def _commands_bit_mux(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = BitMuxFieldInfo(field_type, field_subtype)

        max_delay, labels = yield (
            field_info,
            [
                Get(f"{self.block}1.{field_name}.MAX_DELAY"),
                Get(f"*ENUMS.{self.block}.{field_name}"),
            ],
        )
        assert isinstance(max_delay, str)
        field_info.max_delay = int(max_delay)
        field_info.labels = list(labels)

        return field_name, field_info

    def _commands_pos_mux(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = PosMuxFieldInfo(field_type, field_subtype)

        (labels,) = yield (field_info, [Get(f"*ENUMS.{self.block}.{field_name}")])
        field_info.labels = list(labels)
        return field_name, field_info

    def _commands_pos_out(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = PosOutFieldInfo(field_type, field_subtype)

        (capture,) = yield (
            field_info,
            [Get(f"*ENUMS.{self.block}.{field_name}.CAPTURE")],
        )

        field_info.capture_labels = list(capture)
        return field_name, field_info

    def _commands_ext_out(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = ExtOutFieldInfo(field_type, field_subtype)

        (capture,) = yield (
            field_info,
            [Get(f"*ENUMS.{self.block}.{field_name}.CAPTURE")],
        )

        field_info.capture_labels = list(capture)

        return (
            field_name,
            field_info,
        )

    def _commands_ext_out_bits(
        self, field_name: str, field_type: str, field_subtype: Optional[str]
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        field_info = ExtOutBitsFieldInfo(field_type, field_subtype)

        bits, capture_labels = yield (
            field_info,
            [
                Get(f"{self.block}.{field_name}.BITS"),
                Get(f"*ENUMS.{self.block}.{field_name}.CAPTURE"),
            ],
        )
        field_info.bits = list(bits)
        field_info.capture_labels = list(capture_labels)

        return field_name, field_info

    def _description(
        self, field_name: str, field_info: FieldInfo
    ) -> Generator[
        Tuple[FieldInfo, List[Get]],
        Tuple[Union[List[str], str], ...],
        Tuple[str, FieldInfo],
    ]:
        (desc,) = yield (field_info, [Get(f"*DESC.{self.block}.{field_name}")])
        field_info.description = str(desc)

        return field_name, field_info

    def execute(self) -> ExchangeGenerator[Dict[str, FieldInfo]]:
        ex = Exchange(f"{self.block}.*?")
        yield ex
        unsorted: Dict[int, Tuple[str, FieldInfo]] = {}
        get_cmds_list: List[List[Get]] = []
        generators_list: List[
            Generator[
                Tuple[FieldInfo, List[Get]],
                Tuple[Union[List[str], str], ...],
                Tuple[str, FieldInfo],
            ]
        ] = []
        for line in ex.multiline:
            name, index, type_subtype = line.split(maxsplit=2)

            # Append "None" to list below so there are always at least 2 elements
            # so we can always unpack into subtype, even if no split occurs.
            field_type, subtype, *_ = [*type_subtype.split(maxsplit=1), None]

            # Map a (type, subtype) to a method that returns the appropriate
            # subclasss of FieldInfo, and a list of all the Commands to request
            # TODO: This is static so should live somewhere not inline - but it can't
            # live on the Dataclass itself due to Python restrictions...
            _commands_map: Dict[
                Tuple[str, Optional[str]],
                Callable[
                    [str, str, Optional[str]],
                    Generator[
                        Tuple[FieldInfo, List[Get]],
                        Tuple[Union[List[str], str], ...],
                        Tuple[str, FieldInfo],
                    ],
                ],
            ] = {
                # TODO: No reason to have the "commands" part in all these method names
                # Order matches that of PandA server's Field Types docs
                ("time", None): self._commands_time,
                ("bit_out", None): self._commands_bit_out,
                ("pos_out", None): self._commands_pos_out,
                ("ext_out", "timestamp"): self._commands_ext_out,
                ("ext_out", "samples"): self._commands_ext_out,
                ("ext_out", "bits"): self._commands_ext_out_bits,
                ("bit_mux", None): self._commands_bit_mux,
                ("pos_mux", None): self._commands_pos_mux,
                ("param", "uint"): self._commands_param_uint,
                ("read", "uint"): self._commands_param_uint,
                ("write", "uint"): self._commands_param_uint,
                ("param", "scalar"): self._commands_scalar,
                ("read", "scalar"): self._commands_scalar,
                ("write", "scalar"): self._commands_scalar,
                ("param", "time"): self._commands_subtype_time,
                ("read", "time"): self._commands_subtype_time,
                ("write", "time"): self._commands_subtype_time,
                ("param", "enum"): self._commands_enum,
                ("read", "enum"): self._commands_enum,
                ("write", "enum"): self._commands_enum,
                # TODO: Add TABLE support
            }

            # Always create default FieldInfo. If necessary we will replace it later
            # with a more type-specific version.
            field_info = FieldInfo(field_type, subtype)

            if self.extended_metadata:
                try:
                    # Construct the list of type-specific generators
                    generators_list.append(
                        _commands_map[(field_type, subtype)](name, field_type, subtype)
                    )
                    field_info, get_cmds = next(generators_list[-1])
                    get_cmds_list.append(get_cmds)
                except KeyError:
                    # No type-specific commands to create
                    # Note that this deliberately allows all types and subtypes, to
                    # allow future changes to the server's types without breaking the
                    # clients.
                    # TODO: Add tests for unknown types and subtypes
                    # TODO: Add a warning we encountered an unknown type
                    pass

                # Description is common to all fields
                # Note that we don't get the description for any attributes - these are
                # fixed strings and so not worth retrieving dynamically.
                generators_list.append(self._description(name, field_info))
                field_info, get_cmds = next(generators_list[-1])
                get_cmds_list.append(get_cmds)

            unsorted[int(index)] = (name, field_info)

        # Dict keeps insertion order, so insert in the order the server said
        fields = {name: field for _, (name, field) in sorted(unsorted.items())}

        if self.extended_metadata is False:
            # Asked to not perform the requests for extra metadata.
            return fields

        # Execute the (flattened) list of Get commands
        returned_values = yield from _execute_commands(
            *[item for sublist in get_cmds_list for item in sublist]
        )

        # Convert the flat list of returned_values into a list that matches the shape
        # of the original generators - i.e. a list of lists of responses, where each
        # sub-list's length matches the number of Get requests that generator made.
        items_per_generator = [len(item) for item in get_cmds_list]
        split_vals = []
        idx = 0
        for count in items_per_generator:
            split_vals.append(returned_values[idx : idx + count])
            idx += count

        assert len(split_vals) == len(
            generators_list
        ), f"Mismatched responses received when performing GetFieldInfo({self.block})"

        # Pass the values back to the generators and save the result
        for i, generator in enumerate(generators_list):
            try:
                generator.send(split_vals[i])
            except StopIteration as e:
                field_name, field_info = e.value
                fields[field_name] = field_info

        return fields


class GetPcapBitsLabels(Command):
    """Get the labels for the bit fields in PCAP.

    For example::

        GetPcapBitsLabels() -> {"BITS0" : ["TTLIN1.VAL", "TTLIN2.VAL", ...], ...}
    """

    def execute(self) -> ExchangeGenerator[Dict[str, List[str]]]:
        ex = Exchange("PCAP.*?")
        yield ex
        bits_fields = []
        for line in ex.multiline:
            split = line.split()
            if len(split) == 4:
                field_name, _, field_type, field_subtype = split

                if field_type == "ext_out" and field_subtype == "bits":
                    bits_fields.append(f"PCAP.{field_name}")

        exchanges = [Exchange(f"{field}.BITS?") for field in bits_fields]
        yield exchanges
        bits = {field: ex.multiline for field, ex in zip(bits_fields, exchanges)}
        return bits


class ChangeGroup(Enum):
    """Which group of values to ask for ``*CHANGES`` on:
    https://pandablocks-server.readthedocs.io/en/latest/commands.html#system-commands
    """

    #: All the groups below
    ALL = ""
    #: Configuration settings
    CONFIG = ".CONFIG"
    #: Bits on the system bus
    BITS = ".BITS"
    #: Positions
    POSN = ".POSN"
    #: Polled read values
    READ = ".READ"
    #: Attributes (included capture enable flags)
    ATTR = ".ATTR"
    #: Table changes
    TABLE = ".TABLE"
    #: Table changes
    METADATA = ".METADATA"


@dataclass
class GetChanges(Command[Changes]):
    """Get a `Changes` object showing which fields have changed since the last
    time this was called

    Args:
        group: Restrict to a particular `ChangeGroup`

    For example::

        GetChanges() -> Changes(
            value={"PCAP.TRIG": "PULSE1.OUT"},
            no_value=["SEQ1.TABLE"],
            in_error=["BAD.ENUM"],
        )
    """

    group: ChangeGroup = ChangeGroup.ALL

    def execute(self) -> ExchangeGenerator[Changes]:
        ex = Exchange(f"*CHANGES{self.group.value}?")
        yield ex
        changes = Changes({}, [], [])
        for line in ex.multiline:
            if line[-1] == "<":
                changes.no_value.append(line[:-1])
            elif line.endswith("(error)"):
                changes.in_error.append(line.split(" ", 1)[0])
            else:
                field, value = line.split("=", maxsplit=1)
                changes.values[field] = value
        return changes


@dataclass
class GetState(Command[List[str]]):
    """Get the state of all the fields in a PandA that should be saved as a
    list of raw lines that could be sent with `SetState`.

    For example::

        GetState() -> [
            "SEQ1.TABLE<B"
            "234fds0SDklnmnr"
            ""
            "PCAP.TRIG=PULSE1.OUT",
        ]
    """

    def execute(self) -> ExchangeGenerator[List[str]]:
        # TODO: explain in detail how this works
        # See: references/how-it-works
        attr, config, table, metadata = yield from _execute_commands(
            GetChanges(ChangeGroup.ATTR),
            GetChanges(ChangeGroup.CONFIG),
            GetChanges(ChangeGroup.TABLE),
            GetChanges(ChangeGroup.METADATA),
        )
        # Add the single line values
        line_values = dict(**attr.values, **config.values, **metadata.values)
        state = [f"{k}={v}" for k, v in line_values.items()]
        # Get the multiline values
        multiline_keys, commands = [], []
        for field in table.no_value:
            # Get tables as base64
            multiline_keys.append(f"{field}<B")
            commands.append(Get(f"{field}.B"))
        for field in metadata.no_value:
            # Get metadata as string list
            multiline_keys.append(f"{field}<")
            commands.append(Get(f"{field}"))
        multiline_values = yield from _execute_commands(*commands)
        for k, v in zip(multiline_keys, multiline_values):
            state += [k] + v + [""]
        return state


@dataclass
class SetState(Command[None]):
    """Set the state of all the fields in a PandA

    Args:
        state: A list of raw lines as produced by `GetState`

    For example::

        SetState([
            "SEQ1.TABLE<B"
            "234fds0SDklnmnr"
            ""
            "PCAP.TRIG=PULSE1.OUT",
        ])
    """

    state: List[str]

    def execute(self) -> ExchangeGenerator[None]:
        commands: List[Raw] = []
        command_lines: List[str] = []
        for line in self.state:
            command_lines.append(line)
            first_line = len(command_lines) == 1
            if (first_line and not is_multiline_command(line)) or not line:
                # If not a multiline command
                # Or blank line at the end of a multiline command
                commands.append(Raw(command_lines))
                command_lines = []
        returns = yield from _execute_commands(*commands)
        for command, ret in zip(commands, returns):
            if ret != ["OK"]:
                logging.warning(f"command {command.inp} failed with {ret}")

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

# Define the public API of this module
__all__ = [
    "BlockInfo",
    "FieldInfo",
    "Changes",
    "EndReason",
    "FieldCapture",
    "Data",
    "ReadyData",
    "StartData",
    "FrameData",
    "EndData",
]

# Control


@dataclass
class BlockInfo:
    """Block number and description as exposed by the TCP server

    Attributes:
        number: The index of this block
        description: The description for this block"""

    number: int = 0
    description: Optional[str] = None


@dataclass
class FieldInfo:
    """Field type, subtype, description and labels as exposed by TCP server:
    https://pandablocks-server.readthedocs.io/en/latest/fields.html#field-types

    Attributes:
        type: Field type, like "param", "bit_out", "pos_mux", etc.
        subtype: Some types have subtype, like "uint", "scalar", "lut", etc.
        description: A description of the field
        labels: A list of the valid values for the field when there is a defined list
            of valid values, e.g. those with sub-type "enum"
    """

    type: str
    subtype: Optional[str] = None
    description: Optional[str] = None
    labels: Optional[List[str]] = None

    # Attributes below this point only apply for certain field types and/or subtypes
    # param/read/write - uint
    max: Optional[int] = None

    # param/read/write - scalar
    units: Optional[str] = None
    scale: Optional[float] = None
    offset: Optional[int] = None  # TODO: PandA returns 0 - is this an int or a float 0?

    # time
    # time's units are special in that they alter the value read out -
    # other units fields are just strings that act as suggestions to the reader
    # units_time: Optional[str] = None # given by GetChanges
    time_units_labels: Optional[List[str]] = None
    min: Optional[float] = None

    # bit_out
    capture_word: Optional[str] = None
    # offset: Optional[int] = None

    # bit_mux
    # delay: Optional[int] = None # given by GetChanges
    max_delay: Optional[int] = None

    # pos_out
    # capture: Optional[str] = None # given by GetChanges
    capture_labels: Optional[List[str]] = None
    # offset: Optional[int] = None # given by GetChanges
    # scale: Optional[float] = None # given by GetChanges
    # units: Optional[str] = None # given by GetChanges
    # scaled: Optional[float] = None # given by GetChanges

    # ext_out
    # capture: Optional[str] = None # given by GetChanges
    # capture_labels: Optional[List[str]] = None
    bits: Optional[List[str]] = None


@dataclass
class Changes:
    """The changes returned from a ``*CHANGES`` command"""

    #: Map field -> value for single-line values that were returned
    values: Dict[str, str]
    #: The fields that were present but without value
    no_value: List[str]
    #: The fields that were in error
    in_error: List[str]


# Data


class EndReason(Enum):
    """The reason that a PCAP acquisition completed"""

    #: Experiment completed by falling edge of ``PCAP.ENABLE```
    OK = "Ok"
    #: Experiment manually completed by ``*PCAP.DISARM=`` command
    DISARMED = "Disarmed"
    #: Client disconnect detected
    EARLY_DISCONNECT = "Early disconnect"
    #: Client not taking data quickly or network congestion, internal buffer overflow
    DATA_OVERRUN = "Data overrun"
    #: Triggers too fast for configured data capture
    FRAMING_ERROR = "Framing error"
    #: Probable CPU overload on PandA, should not occur
    DRIVER_DATA_OVERRUN = "Driver data overrun"
    #: Data capture too fast for memory bandwidth
    DMA_DATA_ERROR = "DMA data error"


@dataclass
class FieldCapture:
    """Information about a field that is being captured

    Attributes:
        name: Name of captured field
        type: Numpy data type of the field as transmitted
        capture: Value of CAPTURE field used to enable this field
        scale: Scaling factor, default 1.0
        offset: Offset, default 0.0
        units: Units string, default ""
    """

    name: str
    type: np.dtype
    capture: str
    scale: float = 1.0
    offset: float = 0.0
    units: str = ""


class Data:
    """Baseclass for all responses yielded by a `DataConnection`"""


@dataclass
class ReadyData(Data):
    """Yielded once when the connection is established and ready to take data"""


@dataclass
class StartData(Data):
    """Yielded when a new PCAP acquisition starts.

    Attributes:
        fields: Information about each captured field as a `FieldCapture` object
        missed: Number of samples missed by late data port connection
        process: Data processing option, only "Scaled" or "Raw" are requested
        format: Data delivery formatting, only "Framed" is requested
        sample_bytes: Number of bytes in one sample
    """

    fields: List[FieldCapture]
    missed: int
    process: str
    format: str
    sample_bytes: int


@dataclass
class FrameData(Data):
    """Yielded when a new data frame is flushed.

    Attributes:
        data: A numpy `Structured Array <structured_arrays>`

    Data is structured into complete columns. Each column name is
    ``<name>.<capture>`` from the corresponding `FieldInfo`. Data
    can be accessed with these column names. For example::

        # Table view with 2 captured fields
        >>> import numpy
        >>> data = numpy.array([(0, 10),
        ...       (1, 11),
        ...       (2, 12)],
        ...      dtype=[('COUNTER1.OUT.Value', '<f8'), ('COUNTER2.OUT.Value', '<f8')])
        >>> fdata = FrameData(data)
        >>> fdata.data[0] # Row view
        (0., 10.)
        >>> fdata.column_names # Column names
        ('COUNTER1.OUT.Value', 'COUNTER2.OUT.Value')
        >>> fdata.data['COUNTER1.OUT.Value'] # Column view
        array([0., 1., 2.])
    """

    data: np.ndarray

    @property
    def column_names(self) -> Tuple[str, ...]:
        """Return all the column names"""
        names = self.data.dtype.names
        assert names, f"No column names for {self.data.dtype}"
        return names


@dataclass
class EndData(Data):
    """Yielded when a PCAP acquisition ends.

    Attributes:
        samples: The total number of samples (rows) that were yielded
        reason: The `EndReason` for the end of acquisition
    """

    samples: int
    reason: EndReason

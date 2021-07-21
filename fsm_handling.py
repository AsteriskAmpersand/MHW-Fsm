from ast import Str
from io import BufferedReader, SEEK_SET
from operator import sub
import struct
from typing import Any, List
import construct as C
from construct.core import (Int16ul, Float32l, Int32sl, Int32ul, 
                            Float64l, Int64ul, 
                            Int8ul, Int8sl, Int16sl, Int64sl,
                            Byte, CString, Pass,
                            evaluate, this, Struct,
                            )

from dataclasses import dataclass
import sys
import json

def stream_tell(stream,path):
    return C.stream_tell(stream)

def stream_seek(stream,offset,whence,path):
    C.stream_seek(stream,offset,whence)

# Queued Pointer Handling

@dataclass
class PointerQueuedData:
    pointerOffset: int
    pointerType: C.Construct
    data: Any
    dataType: C.Construct

class DataPointer(C.Subconstruct): 
    def __init__(self, subcon: C.Construct, pointed: C.Construct, tag: str = "ptrData"):
        super().__init__(subcon)
        self.pointed = pointed
        self.tag = tag
    
    def _parse(self, stream, context, path):
        ptrVal = super()._parse(stream, context, path)
        return C.Pointer(ptrVal, self.pointed)._parse(stream, context, path)
    
    def _build(self, obj, stream, context, path):
        if self.tag not in context._params:
            context._params[self.tag] =  []
        context._params[self.tag].append(PointerQueuedData(stream_tell(stream, path), self.subcon, obj, self.pointed))
        super()._build(0, stream, context, path)
        return obj

class DataEntries(C.Construct):
    def __init__(self, tag: str = "ptrData"):
        super().__init__()
        self.tag = tag
        self.flagbuildnone = True

    def _build(self, obj, stream, context, path):
        if self.tag not in context._params:
            return

        for queuedData in context._params[self.tag]:
            pos = stream_tell(stream, path)
            stream_seek(stream, queuedData.pointerOffset, SEEK_SET, path)
            queuedData.pointerType._build(pos, stream, context, path)
            stream_seek(stream, pos, SEEK_SET, path)
            queuedData.dataType._build(queuedData.data, stream, context, path)
        context._params[self.tag].clear()
        return obj

    def _parse(self, stream, context, path):
        pass


def PrefixedOffset(sizetype, type, offs = 0):  
    return C.FocusedSeq("content",
        "_data" / C.Rebuild(C.Struct(
            "size" / C.Rebuild(sizetype, C.len_(this.data) - offs),
            "data" / C.Bytes(this.size + offs)
        ), lambda obj: {"data": type.build(obj.content, **{**obj._params, **obj})}),

        "content" / C.RestreamData(this._data.data, type)
    )

# Class definition handling

ClassMemberDefinition = Struct(
    "name" / DataPointer(Int64ul, CString("utf8"), "names"),
    "type" / Byte,
    "unkn" / Byte,
    "size" / Byte,
    "_unknData" / C.Default(Byte[37], [0 for _ in range(37)]),
)

ClassDefinition = DataPointer(
    Int64ul,
    Struct(
        "hash" / Int64ul,
        "members" / C.PrefixedArray(Int64ul, ClassMemberDefinition)
    ),
    "definitionData")

ClassDefinitionList = C.FocusedSeq(
    "definitions",
    "_count" / C.Rebuild(Int32ul, C.len_(this.definitions)),
    "definitions" / C.Prefixed(
        Int32ul,
        C.Aligned(
            8,
            C.FocusedSeq("definitions",
                       "definitions" /
                       ClassDefinition[this._._count],
                       DataEntries("definitionData"),
                       DataEntries("names"),
                       )))
)

# Hierarchy handling
varcount = 0
def varHandling(this):
    global varcount
    ret = varcount
    varcount += 1
    return ret


def ClassEntry_(): 
    return Struct(
        "_type" / Int16ul,
        "isInstance" / C.Computed(lambda this: this._type & 1),
        "Class_Index" / C.Computed(lambda this: this._type >> 1),
        "_valid" / C.Computed(lambda this: this._type < len(this._root.defs)),
        "index" / Int16ul,
        "content" / C.If(this.isInstance,
            C.LazyBound(lambda: PrefixedOffset(
                Int64ul, ClassImplementation(this._._.Class_Index), -8))
           )
    )

class ClassEntry(C.Adapter):
    def __init__(self):
        super().__init__(ClassEntry_())
    
    def _decode(self, obj, context, path):
        if obj.content is not None:
            obj = {**obj, **obj.content}
            obj.pop("content")
        return obj
    
    def _encode(self, obj, context, path):
        if obj.isInstance:
            global varcount
            varcount += 1
            #print(len(obj))
            #print(obj)
        ret = {"_type": obj.isInstance+(obj.Class_Index<<1) ,
               "index": obj.index,
               "content": obj}
        ret["content"].pop("isInstance")
        ret["content"].pop("Class_Index")
        ret["content"].pop("index")
        return ret

def ClassImpl(id):
  return C.FocusedSeq("classes",
      "_class" / C.Computed(lambda this: this._root.defs[evaluate(id, this)]),
      "classes" / C.FocusedSeq("entries",
          "_index" / C.Index,
          "_member" / C.Computed(lambda this: this._._class.members[this._index]),
          "entries" / C.Sequence(
              C.Computed(this._._member.name),
              DataEntry(lambda this: this._._._member.type)
          )
      )[C.len_(this._class.members)]
  )

class ClassImplementation(C.Adapter):
    def __init__(self, id):
        super().__init__(ClassImpl(id))

    def _decode(self, obj, context, path):
        newdict = {}
        for pair in obj:
            if len(pair[1]) == 1:
                newdict[pair[0]] = pair[1][0]
            else:
                newdict[pair[0]] = pair[1]
        return newdict
    
    def _encode(self, obj, context, path):
        newlist = []
        for k,v in obj.items():
            if not isinstance(v, list):
                v = [v]
            newlist.append([k, v])
        return newlist

def RGBA():
    return Struct(
        "red" / Byte,
        "green" / Byte,
        "blue" / Byte,
        "alpha" / Byte)

def Vector3():
    return Struct(
        "x" / Float32l,
        "y" / Float32l,
        "z" / Float32l,
        "w" / Float32l)

def Vector4():
    return Struct(
        "x" / Float32l,
        "y" / Float32l,
        "z" / Float32l,
        "w" / Float32l)

def Quat4():
    return Struct(
        "x" / Float32l,
        "y" / Float32l,
        "z" / Float32l,
        "w" / Float32l)

def Vector2():
    return Struct(
        "u" / Float32l,
        "v" / Float32l
    )

def DataEntry(type):
    return C.FocusedSeq("values",
        "_count" / C.Rebuild(Int32ul, C.len_(this.values)),
        "values" / C.Switch(type, {
            0: Pass,
            1: ClassEntry(),
            2: ClassEntry(),
            3: Byte, #boolean
            4: Int8ul,
            5: Int16ul,
            6: Int32ul,
            7: Int64ul,
            8: Int8sl,
            9: Int16sl,
            10: Int32sl,
            11: Int64sl,
            12: Float32l,
            13: Float64l,
            14: CString("utf8"),
            15: RGBA(),
            16: Int64ul, #pointer
            #17: Int32ul #size, potentially not a uint but that's probably the best option of it
            20: Vector3(),
            21: Vector4(),
            22: Quat4(),
            32: CString('utf8'), #specifically a CString, while 14 is probably something like std::string
            64: Vector2()
        }, default=C.StopFieldError)[this._count],
    )


# Top-level stuff
Header = Struct(
    "sig" / Byte[4],
    "version" / Int16ul,
    "type" / Int16ul,
    "_classCountPos" / C.Tell,
    "_classCount" / C.Rebuild(Int64ul, lambda _: 0),
)

topLevel = Struct(
    "header" / Header,
    "defs" / ClassDefinitionList,
    "root" / ClassEntry(),
    C.Pointer(this.header._classCountPos, C.Rebuild(Int64ul, varHandling))
)


def filterVariables(node):
    if isinstance(node, dict):
        for key in {**node}:
            if isinstance(key, str) and key.startswith("_"):
                node.pop(key)
        for key in node:
            filterVariables(node[key])
    if isinstance(node, list):
        for val in node:
            filterVariables(val)
    return

def importToContainer(node):
    if isinstance(node, dict):
        return C.Container({k: importToContainer(v) for (k, v) in node.items()})
    if isinstance(node, list):
        return C.ListContainer(importToContainer(i) for i in node)
    return node

    

    
class Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return list(obj)
        if isinstance(obj, BufferedReader):
            return []
        return str(obj)

def decode(path):
    with open(path, 'rb') as f:
        main_dict = topLevel.parse_stream(f)
    filterVariables(main_dict)
    with open(path + ".json", 'w', encoding="utf-8") as f:
        json.dump(main_dict, f, cls=Encoder, indent=True, ensure_ascii=False)

def encode(path):
    with open(path, 'r', encoding="utf-8") as f:
        main_dict = json.load(f)
    main_dict = importToContainer(main_dict)
    with open(path[:-5], 'wb') as f:
        topLevel.build_stream(main_dict, f)

target = sys.argv[1]
if (target.endswith('.json')):
    encode(target)
else:
    decode(target)

"""The game's character and item files, as far as they name models. No Blender dependency.

data/<character>/NN.dat is one character with one weapon. NN is the weapon slot.
    +0x000  u32       leading tag
    +0x004  char[260] the character's .X, relative to its own directory
    +0x114  u32       weapon id, which names data/item/NNN.dat

data/item/NNN.dat (FUN_00433eb0):
    u32 model count, u32 bullet count, char[30] gauge texture, u32 gauge heights[2],
    then per model: char[30] mesh, char[30] trail texture, i32 anchors[9]

An empty mesh name keeps its index, so indices match what the game binds to a hand.
"""

import os
import struct
from dataclasses import dataclass, field

from .xfile import decode_name

_MODEL_BLOCK = 260
_WEAPON_ID_OFFSET = 0x114
_NAME_FIELD = 30
_ITEM_HEADER = 8 + _NAME_FIELD + 8
_ITEM_MODEL = _NAME_FIELD * 2 + 9 * 4


@dataclass
class WeaponModel:
    index: int     # the item's model index, as the game binds it to a hand
    mesh: str
    path: str      # "" when the file named by `mesh` is not on disk


@dataclass
class Character:
    dat_path: str
    x_path: str
    weapon_id: int
    weapons: list = field(default_factory=list)   # non-empty models only


def find_file(directory, name):
    """Case-insensitive: items name 02.x and ship 02.X, which only Windows treats as equal."""
    if not os.path.isdir(directory):
        return ""
    wanted = name.lower()
    for candidate in os.listdir(directory):
        if candidate.lower() == wanted:
            return os.path.join(directory, candidate)
    return ""


def read_item_meshes(data):
    """bytes of data/item/NNN.dat -> mesh name per model index, "" for an empty model."""
    if len(data) < _ITEM_HEADER:
        raise ValueError("item file is too short")
    count = struct.unpack_from("<i", data, 0)[0]
    if not 0 <= count <= 16:
        raise ValueError(f"item file declares {count} models")
    if _ITEM_HEADER + count * _ITEM_MODEL > len(data):
        raise ValueError("item file ends inside its models")
    return [decode_name(data[_ITEM_HEADER + i * _ITEM_MODEL:][:_NAME_FIELD].split(b"\0")[0])
            for i in range(count)]


def read_character(path):
    """data/<character>/NN.dat -> Character, with its .X and item files resolved."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < _WEAPON_ID_OFFSET + 4:
        raise ValueError(f"{path}: too short for a character file")
    model_name = decode_name(data[4:4 + _MODEL_BLOCK].split(b"\0")[0])
    if not model_name.lower().endswith(".x"):
        raise ValueError(f"{path}: not a character file (it names no .X model)")
    directory = os.path.dirname(os.path.abspath(path))
    x_path = find_file(directory, model_name)
    if not x_path:
        raise ValueError(f"{path}: its model {model_name} is not in {directory}")

    weapon_id = struct.unpack_from("<I", data, _WEAPON_ID_OFFSET)[0]
    character = Character(os.path.abspath(path), x_path, weapon_id)
    item_directory = os.path.join(os.path.dirname(directory), "item")
    item_path = find_file(item_directory, f"{weapon_id:03d}.dat")
    if not item_path:
        raise ValueError(f"{path}: weapon {weapon_id} has no {weapon_id:03d}.dat in {item_directory}")
    with open(item_path, "rb") as f:
        meshes = read_item_meshes(f.read())
    for index, mesh in enumerate(meshes):
        if mesh:
            character.weapons.append(WeaponModel(index, mesh, find_file(item_directory, mesh)))
    return character

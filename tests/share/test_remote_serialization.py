"""Tests for remote serialization: as_dict/from_dict convention and wire format.

Any Pixeltable class used in remote ser/des must implement as_dict() and from_dict().
"""

import pytest

from pixeltable.share.remote.remote_schema_objects import RemoteDir, RemoteTable
from pixeltable.share.remote.utils import (
    _serializer_deserializer_from_as_dict_from_dict,
    create_pydantic_model_from_function,
)


class TestRemoteTableRemoteDirAsDictFromDict:
    """RemoteTable and RemoteDir use wire-format as_dict/from_dict."""

    def test_remote_table_as_dict_wire_format(self) -> None:
        t = RemoteTable(path='pxt://org:db/table')
        d = t.as_dict()
        assert d == {'remote_table_path': 'pxt://org:db/table'}

    def test_remote_table_from_dict_wire_format(self) -> None:
        d = {'remote_table_path': 'pxt://org:db/table'}
        t = RemoteTable.from_dict(d)
        assert t.path == 'pxt://org:db/table'

    def test_remote_table_from_dict_legacy_path_key(self) -> None:
        d = {'path': 'pxt://org:db/table'}
        t = RemoteTable.from_dict(d)
        assert t.path == 'pxt://org:db/table'

    def test_remote_table_round_trip(self) -> None:
        t = RemoteTable(path='pxt://org:db/table', remote_metadata={'k': 'v'})
        d = t.as_dict()
        t2 = RemoteTable.from_dict(d)
        assert t2.path == t.path

    def test_remote_table_from_dict_missing_path_raises(self) -> None:
        with pytest.raises(KeyError, match='remote_table_path|path'):
            RemoteTable.from_dict({})

    def test_remote_dir_as_dict_wire_format(self) -> None:
        d_obj = RemoteDir(path='pxt://org:db/dir')
        d = d_obj.as_dict()
        assert d == {'remote_dir_path': 'pxt://org:db/dir'}

    def test_remote_dir_from_dict_wire_format(self) -> None:
        d = {'remote_dir_path': 'pxt://org:db/dir'}
        d_obj = RemoteDir.from_dict(d)
        assert d_obj.path == 'pxt://org:db/dir'

    def test_remote_dir_from_dict_legacy_path_key(self) -> None:
        d = {'path': 'pxt://org:db/dir'}
        d_obj = RemoteDir.from_dict(d)
        assert d_obj.path == 'pxt://org:db/dir'

    def test_remote_dir_round_trip(self) -> None:
        d_obj = RemoteDir(path='pxt://org:db/dir')
        d = d_obj.as_dict()
        d_obj2 = RemoteDir.from_dict(d)
        assert d_obj2.path == d_obj.path

    def test_remote_dir_from_dict_missing_path_raises(self) -> None:
        with pytest.raises(KeyError, match='remote_dir_path|path'):
            RemoteDir.from_dict({})


class TestAsDictFromDictRequired:
    """Pixeltable classes in remote ser/des must have as_dict() and from_dict()."""

    def test_remote_table_has_serializer_deserializer(self) -> None:
        ser, des = _serializer_deserializer_from_as_dict_from_dict(RemoteTable)
        t = RemoteTable(path='pxt://org:db/t')
        d = ser(t)
        assert d == {'remote_table_path': 'pxt://org:db/t'}
        t2 = des(d)
        assert t2.path == t.path

    def test_remote_dir_has_serializer_deserializer(self) -> None:
        ser, des = _serializer_deserializer_from_as_dict_from_dict(RemoteDir)
        d_obj = RemoteDir(path='pxt://org:db/d')
        d = ser(d_obj)
        assert d == {'remote_dir_path': 'pxt://org:db/d'}
        d_obj2 = des(d)
        assert d_obj2.path == d_obj.path

    def test_type_without_as_dict_raises(self) -> None:
        class NoAsDict:
            @classmethod
            def from_dict(cls, d: dict) -> 'NoAsDict':
                return cls()

        with pytest.raises(TypeError, match='must implement as_dict'):
            _serializer_deserializer_from_as_dict_from_dict(NoAsDict)

    def test_type_without_from_dict_raises(self) -> None:
        class NoFromDict:
            def as_dict(self) -> dict:
                return {}

        with pytest.raises(TypeError, match='must implement from_dict'):
            _serializer_deserializer_from_as_dict_from_dict(NoFromDict)


class TestCreatePydanticModelUsesAsDictFromDict:
    """create_pydantic_model_from_function uses as_dict/from_dict for RemoteTable/RemoteDir."""

    def test_model_serializes_remote_table_via_as_dict(
        self,
    ) -> None:
        def fn(t: RemoteTable) -> str:
            return t.path

        model_cls, _ = create_pydantic_model_from_function(fn)
        t = RemoteTable(path='pxt://org:db/table')
        instance = model_cls(t=t)
        d = instance.model_dump()
        assert d['t'] == {'remote_table_path': 'pxt://org:db/table'}

    def test_model_deserializes_remote_table_via_from_dict(
        self,
    ) -> None:
        def fn(t: RemoteTable) -> str:
            return t.path

        model_cls, _ = create_pydantic_model_from_function(fn)
        instance = model_cls(t={'remote_table_path': 'pxt://org:db/table'})
        assert instance.t.path == 'pxt://org:db/table'

    def test_model_serializes_remote_dir_via_as_dict(
        self,
    ) -> None:
        def fn(d: RemoteDir) -> str:
            return d.path

        model_cls, _ = create_pydantic_model_from_function(fn)
        d_obj = RemoteDir(path='pxt://org:db/dir')
        instance = model_cls(d=d_obj)
        d = instance.model_dump()
        assert d['d'] == {'remote_dir_path': 'pxt://org:db/dir'}

    def test_model_deserializes_remote_dir_via_from_dict(
        self,
    ) -> None:
        def fn(d: RemoteDir) -> str:
            return d.path

        model_cls, _ = create_pydantic_model_from_function(fn)
        instance = model_cls(d={'remote_dir_path': 'pxt://org:db/dir'})
        assert instance.d.path == 'pxt://org:db/dir'

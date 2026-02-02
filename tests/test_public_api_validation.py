"""
Tests for @public decorator: input validation via Pydantic models for public APIs.

These tests demonstrate the approach so the team can decide on adopting
@public + Pydantic validation for create_table, create_view, create_snapshot, etc.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import pixeltable as pxt


class TestPublicApiValidation:
    """@public decorator validates inputs using Pydantic models built from function signatures."""

    def test_create_table_valid_inputs(self, init_env: None) -> None:
        """Valid inputs pass validation and create_table succeeds."""
        t = pxt.create_table('public_api_test_table', schema={'id': pxt.Int, 'name': pxt.String})
        assert t is not None
        assert t._path() == 'public_api_test_table'
        # Cleanup
        pxt.drop_table('public_api_test_table', if_not_exists='ignore')

    def test_create_table_invalid_if_exists_raises(self, init_env: None) -> None:
        """Invalid value for Literal parameter (e.g. if_exists) raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            pxt.create_table(
                't',
                schema={'x': pxt.Int},
                if_exists='not_a_valid_option',  # type: ignore[arg-type]
            )
        err = exc_info.value
        assert 'if_exists' in str(err) or 'not_a_valid_option' in str(err)

    def test_create_table_missing_required_path_raises(self, init_env: None) -> None:
        """Missing required parameter (path) raises ValidationError when passed as None via kwargs."""
        with pytest.raises(ValidationError) as exc_info:
            pxt.create_table(None, schema={'x': pxt.Int})  # type: ignore[arg-type]
        err = exc_info.value
        assert 'path' in str(err).lower() or 'required' in str(err).lower()

    def test_create_view_valid_inputs(self, init_env: None) -> None:
        """Valid inputs pass validation and create_view succeeds."""
        pxt.create_table('base_for_view', schema={'id': pxt.Int})
        tbl = pxt.get_table('base_for_view')
        view = pxt.create_view('public_api_test_view', tbl)
        assert view is not None
        pxt.drop_table('public_api_test_view', if_not_exists='ignore')
        pxt.drop_table('base_for_view', if_not_exists='ignore')

    def test_create_view_invalid_if_exists_raises(self, init_env: None) -> None:
        """Invalid if_exists for create_view raises ValidationError."""
        pxt.create_table('base_v', schema={'id': pxt.Int})
        tbl = pxt.get_table('base_v')
        with pytest.raises(ValidationError) as exc_info:
            pxt.create_view('v', tbl, if_exists='invalid')  # type: ignore[arg-type]
        err = exc_info.value
        assert 'if_exists' in str(err) or 'invalid' in str(err)
        pxt.drop_table('base_v', if_not_exists='ignore')

    def test_create_snapshot_valid_inputs(self, init_env: None) -> None:
        """Valid inputs pass validation and create_snapshot succeeds."""
        pxt.create_table('base_for_snap', schema={'id': pxt.Int})
        tbl = pxt.get_table('base_for_snap')
        snap = pxt.create_snapshot('public_api_test_snapshot', tbl)
        assert snap is not None
        pxt.drop_table('public_api_test_snapshot', if_not_exists='ignore')
        pxt.drop_table('base_for_snap', if_not_exists='ignore')

    def test_create_snapshot_invalid_media_validation_raises(self, init_env: None) -> None:
        """Invalid media_validation for create_snapshot raises ValidationError."""
        pxt.create_table('base_s', schema={'id': pxt.Int})
        tbl = pxt.get_table('base_s')
        with pytest.raises(ValidationError) as exc_info:
            pxt.create_snapshot('snap', tbl, media_validation='invalid')  # type: ignore[arg-type]
        err = exc_info.value
        assert 'media_validation' in str(err) or 'invalid' in str(err)
        pxt.drop_table('base_s', if_not_exists='ignore')

    def test_public_decorator_preserves_signature(self) -> None:
        """@public preserves the wrapped function's signature and __name__."""
        assert hasattr(pxt.create_table, '_is_public')
        assert pxt.create_table.__name__ == 'create_table'
        import inspect
        sig = inspect.signature(pxt.create_table)
        params = list(sig.parameters.keys())
        assert 'path' in params
        assert 'schema' in params

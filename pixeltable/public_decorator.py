"""
Public API decorator for Pixeltable functions.

Marks functions as public APIs and validates inputs using Pydantic models
built from the function signature. Use for create_table, create_view,
create_snapshot, and other public entrypoints where input typechecking is desired.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable, Optional, ParamSpec, TypeVar, cast

from pydantic import ValidationError

P = ParamSpec('P')
T = TypeVar('T')

_logger = logging.getLogger('pixeltable.public_api')

# Types that appear in globals.py signatures as forward refs; use Any for validation
_MODEL_REBUILD_NAMESPACE = {'TableDataSource': Any, 'Optional': Optional}


def public(func: Callable[P, T]) -> Callable[P, T]:
    """
    Decorator to mark a function as a public API and validate its inputs via a Pydantic model.

    On each call, arguments are validated by constructing a Pydantic model from the function's
    signature (reusing the same model builder as @remote). If validation fails, Pydantic's
    ValidationError is raised. If it passes, the original function is called with the
    validated (and possibly coerced) values.

    Use for public entrypoints like create_table, create_view, create_snapshot so that
    invalid input types or missing required args are caught before the implementation runs.
    """

    # Resolve to the underlying function if already wrapped (e.g. by @remote)
    original_func = getattr(func, '_original_func', func)
    func_name = original_func.__name__

    # Cache for the Pydantic model and field mapping (lazy, built on first call)
    _model_cache: tuple[Any, dict[str, str]] | None = None

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal _model_cache
        if _model_cache is None:
            from pixeltable.share.remote.utils import create_pydantic_model_from_function

            try:
                model_cls, field_mapping = create_pydantic_model_from_function(original_func, func_name)
                try:
                    model_cls.model_rebuild(_types_namespace=_MODEL_REBUILD_NAMESPACE)
                except Exception:
                    pass
                _model_cache = (model_cls, field_mapping)
            except Exception as e:
                _logger.warning(
                    'Could not build Pydantic model for @public %s: %s. Skipping validation.',
                    func_name,
                    e,
                )
                return func(*args, **kwargs)

        model_cls, field_mapping = _model_cache
        sig = inspect.signature(original_func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)

        # Map to model field names (some params are renamed for Pydantic, e.g. if_exists -> if_exists_param)
        # field_mapping: model_field_name -> original_param_name
        model_kwargs: dict[str, Any] = {}
        for param_name, value in arguments.items():
            model_field_name = next((m for m, o in field_mapping.items() if o == param_name), param_name)
            model_kwargs[model_field_name] = value

        try:
            validated = model_cls(**model_kwargs)
        except ValidationError as e:
            raise e

        # Build kwargs for the original function: field_mapping gives original name for each model field
        out_kwargs: dict[str, Any] = {}
        for model_field_name in model_cls.model_fields:
            original_name = field_mapping.get(model_field_name, model_field_name)
            out_kwargs[original_name] = getattr(validated, model_field_name)

        return func(**out_kwargs)

    wrapper._is_public = True  # type: ignore[attr-defined]
    wrapper._original_func = original_func  # type: ignore[attr-defined]
    return cast(Callable[P, T], wrapper)

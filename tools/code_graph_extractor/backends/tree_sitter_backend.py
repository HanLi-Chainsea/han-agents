"""
Tree-sitter AST-based Extractor Backend

Accurate multi-language code extraction using Tree-sitter AST parsing.
Supports: Python, TypeScript/JavaScript, Go, Java, Rust, C, C++

Advantages over regex:
- Class method extraction
- Call graph extraction
- Accurate scope tracking
- Handles all formatting edge cases

Requires: pip install tree-sitter tree-sitter-{python,javascript,typescript,java,rust,go}
"""

import os
from typing import Set, Optional, Dict, List, Any, Callable
from dataclasses import dataclass, field

import sys
import subprocess
import threading
import warnings

try:
    import tree_sitter
    _HAS_TREE_SITTER = True
except ImportError:
    tree_sitter = None  # type: ignore[assignment]
    _HAS_TREE_SITTER = False

from tools.code_graph_extractor.extractor import (
    CodeNode, CodeEdge, ExtractionResult,
    make_node_id, compute_file_hash,
)


# =============================================================================
# Language Grammar Loaders (lazy)
# =============================================================================

_GRAMMAR_CACHE: Dict[str, Any] = {}  # maps language -> tree_sitter.Language or None (permanent failure)
_GRAMMAR_LOCK = threading.Lock()

# Set HAN_NO_INSTALL=1 to disable auto-installation (for CI/air-gapped environments)
_AUTO_INSTALL_ENABLED = os.environ.get('HAN_NO_INSTALL', '').lower() not in ('1', 'true', 'yes')

# Package names for pip install
_PIP_GRAMMAR_PACKAGES: Dict[str, str] = {
    'python': 'tree-sitter-python',
    'typescript': 'tree-sitter-typescript',
    'javascript': 'tree-sitter-javascript',
    'java': 'tree-sitter-java',
    'rust': 'tree-sitter-rust',
    'go': 'tree-sitter-go',
    'c': 'tree-sitter-c',
    'cpp': 'tree-sitter-cpp',
}


def _auto_install_grammar(language: str) -> bool:
    """Auto-install tree-sitter core and grammar package via pip. Returns True on success.

    Disabled when HAN_NO_INSTALL=1 environment variable is set.
    """
    if not _AUTO_INSTALL_ENABLED:
        return False

    grammar_pkg = _PIP_GRAMMAR_PACKAGES.get(language)
    if not grammar_pkg:
        return False

    packages = ['tree-sitter', grammar_pkg]
    print(f"[HAN] Installing tree-sitter grammar for {language}...")
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--quiet'] + packages,
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True
        warnings.warn(
            f"Failed to auto-install tree-sitter for {language}. "
            f"Run manually: pip install -r requirements-ast.txt"
        )
        return False
    except Exception as exc:
        warnings.warn(
            f"Failed to auto-install tree-sitter for {language}: {type(exc).__name__}. "
            f"Run manually: pip install -r requirements-ast.txt"
        )
        return False


_LOADER_MAP = {
    'python': ('tree_sitter_python', 'language'),
    'typescript': ('tree_sitter_typescript', 'language_typescript'),
    'javascript': ('tree_sitter_javascript', 'language'),
    'java': ('tree_sitter_java', 'language'),
    'rust': ('tree_sitter_rust', 'language'),
    'go': ('tree_sitter_go', 'language'),
    'c': ('tree_sitter_c', 'language'),
    'cpp': ('tree_sitter_cpp', 'language'),
}


def _load_grammar(language: str, auto_install: bool = True) -> Optional[Any]:
    """Load tree-sitter grammar for a language. Returns None if unavailable.

    Args:
        language: Language name (e.g. 'python', 'typescript')
        auto_install: If True, attempt pip install on missing grammars.
                      Set False for capability probes (can_handle) to avoid side effects.
    """
    global _HAS_TREE_SITTER, tree_sitter

    # Fast path: already cached (None = permanent failure, grammar = success)
    if language in _GRAMMAR_CACHE:
        return _GRAMMAR_CACHE[language]

    # Unsupported language — no loader spec exists
    if language not in _LOADER_MAP:
        return None

    with _GRAMMAR_LOCK:
        # Double-check after acquiring lock
        if language in _GRAMMAR_CACHE:
            return _GRAMMAR_CACHE[language]

        # Ensure tree-sitter core is available
        if not _HAS_TREE_SITTER:
            if auto_install and _auto_install_grammar(language):
                try:
                    import importlib
                    tree_sitter = importlib.import_module('tree_sitter')
                    _HAS_TREE_SITTER = True
                except ImportError:
                    pass
            if not _HAS_TREE_SITTER:
                # Don't cache — tree-sitter might be installed later
                return None

        module_name, func_name = _LOADER_MAP[language]

        def _try_import() -> Optional[Any]:
            try:
                import importlib
                mod = importlib.import_module(module_name)
                lang_func = getattr(mod, func_name)
                return tree_sitter.Language(lang_func())
            except (ImportError, AttributeError, OSError, TypeError, ValueError):
                return None

        install_succeeded = False
        grammar = _try_import()
        if grammar is None and auto_install:
            # Try auto-install once, then retry
            install_succeeded = _auto_install_grammar(language)
            if install_succeeded:
                grammar = _try_import()

        if grammar is not None:
            # Cache successful load
            _GRAMMAR_CACHE[language] = grammar
        elif install_succeeded:
            # Install succeeded but import still failed — permanent failure (incompatible version etc.)
            _GRAMMAR_CACHE[language] = None
        # else: install failed (transient) or probe-only — don't cache, allow retry later

        return grammar


# =============================================================================
# Language Query Packs
# =============================================================================

@dataclass
class LanguageQueryPack:
    """Language-specific extraction rules for AST walking."""
    language: str

    # AST node type mappings
    class_types: List[str] = field(default_factory=list)
    function_types: List[str] = field(default_factory=list)
    method_types: List[str] = field(default_factory=list)
    interface_types: List[str] = field(default_factory=list)
    import_types: List[str] = field(default_factory=list)
    call_types: List[str] = field(default_factory=list)
    type_alias_types: List[str] = field(default_factory=list)
    constant_types: List[str] = field(default_factory=list)
    module_types: List[str] = field(default_factory=list)

    # Extraction hooks (language-specific logic)
    extract_class: Optional[Callable] = None
    extract_function: Optional[Callable] = None
    extract_method: Optional[Callable] = None
    extract_import: Optional[Callable] = None
    extract_call: Optional[Callable] = None
    extract_constant: Optional[Callable] = None
    extract_interface: Optional[Callable] = None
    extract_type_alias: Optional[Callable] = None
    extract_module: Optional[Callable] = None

    # 若為 True，interface 成員會以 interface 名作 scope 提取
    # （Java：interface 方法是 API 契約的一級公民；其他語言維持原行為）
    interface_members_scoped: bool = False

    # Visibility detection
    detect_visibility: Optional[Callable] = None


# =============================================================================
# Python Query Pack
# =============================================================================

def _py_extract_class(node) -> Dict:
    name_node = node.child_by_field_name('name')
    bases_node = node.child_by_field_name('superclasses')
    bases = []
    if bases_node:
        for child in bases_node.named_children:
            bases.append(child.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _py_extract_function(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    is_async = any(c.type == 'async' for c in node.children)
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'is_async': is_async,
    }


def _py_extract_import(node) -> Dict:
    text = node.text.decode()
    if node.type == 'import_from_statement':
        module_node = node.child_by_field_name('module_name')
        module = module_node.text.decode() if module_node else ''
        names = []
        for child in node.named_children:
            if child.type in ('dotted_name', 'aliased_import') and child != module_node:
                names.append(child.text.decode())
        return {'module': module, 'names': names, 'text': text}
    else:
        # import X
        names = []
        for child in node.named_children:
            if child.type in ('dotted_name', 'aliased_import'):
                names.append(child.text.decode())
        return {'module': '', 'names': names, 'text': text}


def _py_extract_constant(node) -> Optional[Dict]:
    """Check if expression_statement contains UPPER_CASE = ..."""
    if node.type != 'expression_statement':
        return None
    for child in node.children:
        if child.type == 'assignment':
            left = child.child_by_field_name('left')
            if left and left.type == 'identifier':
                name = left.text.decode()
                if name.isupper() or (name[0].isupper() and '_' in name and name.replace('_', '').isalpha()):
                    return {'name': name}
    return None


def _py_detect_visibility(name: str, **kwargs) -> str:
    if name.startswith('__') and name.endswith('__'):
        return 'public'  # dunder methods
    if name.startswith('_'):
        return 'private'
    return 'public'


PYTHON_PACK = LanguageQueryPack(
    language='python',
    class_types=['class_definition'],
    function_types=['function_definition'],
    method_types=[],  # methods are function_definition inside class
    interface_types=[],
    import_types=['import_statement', 'import_from_statement'],
    call_types=['call'],
    type_alias_types=[],
    constant_types=['expression_statement'],
    module_types=[],
    extract_class=_py_extract_class,
    extract_function=_py_extract_function,
    extract_import=_py_extract_import,
    extract_constant=_py_extract_constant,
    detect_visibility=_py_detect_visibility,
)


# =============================================================================
# TypeScript/JavaScript Query Pack
# =============================================================================

def _ts_extract_class(node) -> Dict:
    name_node = node.child_by_field_name('name')
    bases = []
    # Check heritage clauses (extends, implements)
    for child in node.children:
        if child.type == 'class_heritage':
            for clause in child.named_children:
                if clause.type == 'extends_clause':
                    for val in clause.named_children:
                        if val.type in ('identifier', 'member_expression'):
                            bases.append(val.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _ts_extract_function(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    is_async = any(c.type == 'async' for c in node.children)
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'is_async': is_async,
    }


def _ts_extract_method(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    is_async = any(c.type == 'async' for c in node.children)
    is_static = any(c.type == 'static' for c in node.children)
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'is_async': is_async,
        'is_static': is_static,
    }


def _ts_extract_import(node) -> Dict:
    text = node.text.decode()
    source_node = node.child_by_field_name('source')
    source = source_node.text.decode().strip("'\"") if source_node else ''
    return {'module': source, 'names': [], 'text': text}


def _ts_extract_interface(node) -> Dict:
    name_node = node.child_by_field_name('name')
    bases = []
    for child in node.children:
        if child.type == 'extends_type_clause':
            for t in child.named_children:
                if t.type in ('type_identifier', 'generic_type'):
                    bases.append(t.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _ts_extract_type_alias(node) -> Dict:
    name_node = node.child_by_field_name('name')
    return {'name': name_node.text.decode() if name_node else ''}


def _ts_extract_constant(node) -> Optional[Dict]:
    """Extract const X = ... from lexical_declaration."""
    if node.type != 'lexical_declaration':
        return None
    # Check if it's a const (not let/var)
    for child in node.children:
        if child.type == 'const':
            break
    else:
        return None
    # Get the variable declarator
    for child in node.named_children:
        if child.type == 'variable_declarator':
            name_node = child.child_by_field_name('name')
            if name_node:
                name = name_node.text.decode()
                # Check if it's UPPER_CASE constant or arrow function
                value_node = child.child_by_field_name('value')
                if value_node and value_node.type in ('arrow_function',):
                    return {'name': name, 'is_function': True}
                if name.isupper() or (name[0].isupper() and '_' in name):
                    return {'name': name, 'is_function': False}
                # Also export const functions
                return {'name': name, 'is_function': value_node.type == 'arrow_function' if value_node else False}
    return None


def _ts_detect_visibility(name: str, node=None, **kwargs) -> str:
    if node:
        for child in node.children:
            if child.type in ('public', 'private', 'protected'):
                return child.type
    return 'public'


TYPESCRIPT_PACK = LanguageQueryPack(
    language='typescript',
    class_types=['class_declaration'],
    function_types=['function_declaration'],
    method_types=['method_definition'],
    interface_types=['interface_declaration'],
    import_types=['import_statement'],
    call_types=['call_expression'],
    type_alias_types=['type_alias_declaration'],
    constant_types=['lexical_declaration'],
    module_types=[],
    extract_class=_ts_extract_class,
    extract_function=_ts_extract_function,
    extract_method=_ts_extract_method,
    extract_import=_ts_extract_import,
    extract_interface=_ts_extract_interface,
    extract_type_alias=_ts_extract_type_alias,
    extract_constant=_ts_extract_constant,
    detect_visibility=_ts_detect_visibility,
)


# =============================================================================
# Go Query Pack
# =============================================================================

def _go_extract_class(node) -> Dict:
    """Go type_spec for struct/interface."""
    name_node = node.child_by_field_name('name')
    return {'name': name_node.text.decode() if name_node else '', 'bases': []}


def _go_extract_function(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'is_async': False,
    }


def _go_extract_method(node) -> Dict:
    """Go method_declaration: func (receiver) Name(params) returns."""
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    # Get receiver type
    receiver_type = ''
    for child in node.named_children:
        if child.type == 'parameter_list' and child != params_node:
            # First parameter_list is the receiver
            for param in child.named_children:
                if param.type == 'parameter_declaration':
                    type_node = param.child_by_field_name('type')
                    if type_node:
                        receiver_type = type_node.text.decode().strip('*')
            break
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'is_async': False,
        'receiver_type': receiver_type,
    }


def _go_extract_import(node) -> Dict:
    text = node.text.decode()
    paths = []
    for child in node.named_children:
        if child.type == 'import_spec_list':
            for spec in child.named_children:
                if spec.type == 'import_spec':
                    for lit in spec.named_children:
                        if lit.type == 'interpreted_string_literal':
                            paths.append(lit.text.decode().strip('"'))
        elif child.type == 'import_spec':
            for lit in child.named_children:
                if lit.type == 'interpreted_string_literal':
                    paths.append(lit.text.decode().strip('"'))
    return {'module': '', 'names': paths, 'text': text}


def _go_extract_interface(node) -> Dict:
    """Extract from type_spec where child is interface_type."""
    name_node = node.child_by_field_name('name')
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': [],
    }


def _go_detect_visibility(name: str, **kwargs) -> str:
    return 'public' if name and name[0].isupper() else 'private'


GO_PACK = LanguageQueryPack(
    language='go',
    class_types=['type_spec'],       # struct types
    function_types=['function_declaration'],
    method_types=['method_declaration'],
    interface_types=['type_spec'],    # interface types (filtered by child type)
    import_types=['import_declaration'],
    call_types=['call_expression'],
    type_alias_types=[],
    constant_types=[],
    module_types=[],
    extract_class=_go_extract_class,
    extract_function=_go_extract_function,
    extract_method=_go_extract_method,
    extract_import=_go_extract_import,
    extract_interface=_go_extract_interface,
    detect_visibility=_go_detect_visibility,
)


# =============================================================================
# Java Query Pack
# =============================================================================

def _java_extract_class(node) -> Dict:
    name_node = node.child_by_field_name('name')
    bases = []
    for child in node.children:
        if child.type == 'superclass':
            for t in child.named_children:
                bases.append(t.text.decode())
        elif child.type == 'super_interfaces':
            for t in child.named_children:
                if t.type == 'type_list':
                    for iface in t.named_children:
                        bases.append(iface.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _java_extract_function(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    return_type = node.child_by_field_name('type')
    modifiers = node.child_by_field_name('modifiers') or node.children[0] if node.children else None
    is_static = False
    if modifiers and modifiers.type == 'modifiers':
        is_static = any(c.text.decode() == 'static' for c in modifiers.children)
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'return_type': return_type.text.decode() if return_type else '',
        'is_async': False,
        'is_static': is_static,
    }


def _java_extract_import(node) -> Dict:
    text = node.text.decode()
    # import com.example.Class;
    for child in node.named_children:
        if child.type == 'scoped_identifier':
            return {'module': child.text.decode(), 'names': [], 'text': text}
    return {'module': '', 'names': [], 'text': text}


def _java_detect_visibility(name: str, node=None, **kwargs) -> str:
    if node:
        modifiers = node.child_by_field_name('modifiers')
        if not modifiers:
            for child in node.children:
                if child.type == 'modifiers':
                    modifiers = child
                    break
        if modifiers:
            text = modifiers.text.decode()
            if 'private' in text:
                return 'private'
            if 'protected' in text:
                return 'protected'
            if 'public' in text:
                return 'public'
    return 'package'


def _java_extract_interface(node) -> Dict:
    """Java interface：name + extends 清單（extends_interfaces > type_list）。"""
    name_node = node.child_by_field_name('name')
    bases = []
    for child in node.children:
        if child.type == 'extends_interfaces':
            for t in child.named_children:
                if t.type == 'type_list':
                    for iface in t.named_children:
                        bases.append(iface.text.decode())
                else:
                    bases.append(t.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


JAVA_PACK = LanguageQueryPack(
    language='java',
    class_types=['class_declaration', 'enum_declaration'],
    function_types=['method_declaration', 'constructor_declaration'],
    method_types=[],  # methods are method_declaration inside class body
    interface_types=['interface_declaration', 'annotation_type_declaration'],
    import_types=['import_declaration'],
    call_types=['method_invocation'],
    type_alias_types=[],
    constant_types=[],
    module_types=['package_declaration'],
    extract_class=_java_extract_class,
    extract_function=_java_extract_function,
    extract_import=_java_extract_import,
    extract_interface=_java_extract_interface,
    interface_members_scoped=True,
    detect_visibility=_java_detect_visibility,
)


# =============================================================================
# Rust Query Pack
# =============================================================================

def _rust_extract_class(node) -> Dict:
    """Rust struct/enum."""
    name_node = node.child_by_field_name('name')
    return {'name': name_node.text.decode() if name_node else '', 'bases': []}


def _rust_extract_function(node) -> Dict:
    name_node = node.child_by_field_name('name')
    params_node = node.child_by_field_name('parameters')
    return_type = node.child_by_field_name('return_type')
    is_async = any(c.type == 'async' for c in node.children)
    is_unsafe = any(c.type == 'unsafe' for c in node.children)
    return {
        'name': name_node.text.decode() if name_node else '',
        'signature': params_node.text.decode() if params_node else '()',
        'return_type': return_type.text.decode().lstrip('-> ').strip() if return_type else '',
        'is_async': is_async,
        'is_unsafe': is_unsafe,
    }


def _rust_extract_import(node) -> Dict:
    text = node.text.decode()
    return {'module': '', 'names': [], 'text': text}


def _rust_extract_trait(node) -> Dict:
    name_node = node.child_by_field_name('name')
    bases = []
    for child in node.children:
        if child.type == 'trait_bounds':
            for bound in child.named_children:
                bases.append(bound.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _rust_extract_module(node) -> Dict:
    name_node = node.child_by_field_name('name')
    return {'name': name_node.text.decode() if name_node else ''}


def _rust_detect_visibility(name: str, node=None, **kwargs) -> str:
    if node:
        for child in node.children:
            if child.type == 'visibility_modifier':
                text = child.text.decode()
                if 'pub' in text:
                    return 'public'
    return 'private'


RUST_PACK = LanguageQueryPack(
    language='rust',
    class_types=['enum_item'],  # struct_item handled specially for kind='struct'
    function_types=['function_item'],
    method_types=[],  # methods are function_item inside impl_item
    interface_types=['trait_item'],
    import_types=['use_declaration'],
    call_types=['call_expression'],
    type_alias_types=['type_item'],
    constant_types=['const_item', 'static_item'],
    module_types=['mod_item'],
    extract_class=_rust_extract_class,
    extract_function=_rust_extract_function,
    extract_import=_rust_extract_import,
    extract_interface=_rust_extract_trait,
    extract_module=_rust_extract_module,
    detect_visibility=_rust_detect_visibility,
)


# =============================================================================
# C Query Pack
# =============================================================================

def _c_declarator_name(node):
    """Walk function_declarator/pointer_declarator/array_declarator to find identifier."""
    if node is None:
        return ''
    t = node.type
    if t in ('identifier', 'type_identifier', 'field_identifier'):
        return node.text.decode()
    if t == 'qualified_identifier':
        # C++ Bar::hello → return full qualified name
        return node.text.decode()
    # walk common wrappers
    inner = node.child_by_field_name('declarator')
    if inner:
        return _c_declarator_name(inner)
    for child in node.named_children:
        name = _c_declarator_name(child)
        if name:
            return name
    return ''


def _c_extract_function(node) -> Dict:
    """Extract C/C++ function_definition."""
    decl = node.child_by_field_name('declarator')
    name = _c_declarator_name(decl)
    # parameters are under function_declarator
    sig = '()'
    if decl:
        params = decl.child_by_field_name('parameters')
        if params:
            sig = params.text.decode()
    return {
        'name': name,
        'signature': sig,
        'is_async': False,
    }


def _c_extract_import(node) -> Dict:
    """Extract #include directive."""
    text = node.text.decode()
    module = ''
    for child in node.children:
        if child.type in ('system_lib_string', 'string_literal'):
            module = child.text.decode().strip('<>"')
            break
    return {'module': module, 'names': [], 'text': text}


def _c_extract_constant(node) -> Optional[Dict]:
    """Extract #define NAME or static const TYPE NAME = ..."""
    if node.type == 'preproc_def':
        name_node = node.child_by_field_name('name')
        if name_node:
            return {'name': name_node.text.decode()}
        return None
    if node.type == 'declaration':
        # check for const qualifier
        has_const = any(c.type == 'type_qualifier' and b'const' in c.text for c in node.children)
        if not has_const:
            return None
        for child in node.named_children:
            if child.type == 'init_declarator':
                name = _c_declarator_name(child.child_by_field_name('declarator'))
                if name:
                    return {'name': name}
    return None


C_PACK = LanguageQueryPack(
    language='c',
    class_types=[],
    function_types=['function_definition'],
    method_types=[],
    interface_types=[],
    import_types=['preproc_include'],
    call_types=['call_expression'],
    type_alias_types=[],
    constant_types=['preproc_def', 'declaration'],
    module_types=[],
    extract_function=_c_extract_function,
    extract_import=_c_extract_import,
    extract_constant=_c_extract_constant,
)


# =============================================================================
# C++ Query Pack
# =============================================================================

def _cpp_extract_class(node) -> Dict:
    """Extract class_specifier / struct_specifier (C++ uses struct like class)."""
    name_node = node.child_by_field_name('name')
    bases = []
    for child in node.children:
        if child.type == 'base_class_clause':
            for c in child.named_children:
                if c.type in ('type_identifier', 'qualified_identifier', 'template_type'):
                    bases.append(c.text.decode())
    return {
        'name': name_node.text.decode() if name_node else '',
        'bases': bases,
    }


def _cpp_extract_module(node) -> Dict:
    """Extract namespace_definition → module-like."""
    name_node = node.child_by_field_name('name')
    return {
        'name': name_node.text.decode() if name_node else '<anonymous>',
        'path': name_node.text.decode() if name_node else '',
    }


def _cpp_detect_visibility(name: str, node=None, **kwargs) -> str:
    # C++ visibility is lexically scoped via access_specifier labels — default public
    return 'public'


CPP_PACK = LanguageQueryPack(
    language='cpp',
    class_types=['class_specifier'],
    function_types=['function_definition'],
    method_types=[],
    interface_types=[],
    import_types=['preproc_include'],
    call_types=['call_expression'],
    type_alias_types=[],  # handled directly: type_definition → _handle_c_typedef; alias_declaration → _handle_cpp_alias
    constant_types=['preproc_def', 'declaration'],
    module_types=['namespace_definition'],
    extract_class=_cpp_extract_class,
    extract_function=_c_extract_function,  # reuse C function extractor
    extract_import=_c_extract_import,
    extract_constant=_c_extract_constant,
    extract_module=_cpp_extract_module,
    detect_visibility=_cpp_detect_visibility,
)


# =============================================================================
# Query Pack Registry
# =============================================================================

QUERY_PACKS: Dict[str, LanguageQueryPack] = {
    'python': PYTHON_PACK,
    'typescript': TYPESCRIPT_PACK,
    'javascript': TYPESCRIPT_PACK,  # shared
    'java': JAVA_PACK,
    'rust': RUST_PACK,
    'go': GO_PACK,
    'c': C_PACK,
    'cpp': CPP_PACK,
}


# =============================================================================
# AST Walker Engine (shared across all languages)
# =============================================================================

class ASTWalker:
    """
    Generic AST walker that uses LanguageQueryPack to extract code structure.
    Handles scope tracking, class context, and node/edge generation.
    """

    def __init__(self, pack: LanguageQueryPack, file_path: str, abs_file_path: str = None):
        self.pack = pack
        self.file_path = file_path
        self._abs_file_path = abs_file_path or file_path  # for file_hash computation
        self.nodes: List[CodeNode] = []
        self.edges: List[CodeEdge] = []
        self._class_stack: List[tuple] = []  # [(name, kind)] track nested class context
        self._impl_type: Optional[str] = None  # for Rust impl blocks
        self._known_classes: Dict[str, str] = {}  # simple_name -> kind ('class'|'struct'|'interface')

    def walk(self, root_node) -> ExtractionResult:
        """Walk the AST and extract all code structure."""
        # Add file node
        file_node = CodeNode(
            id=make_node_id('file', self.file_path),
            kind='file',
            name=os.path.basename(self.file_path),
            file_path=self.file_path,
            line_start=1,
            line_end=root_node.end_point[0] + 1,
            language=self.pack.language,
        )
        self.nodes.append(file_node)

        # Walk the tree
        self._visit(root_node)

        return ExtractionResult(
            nodes=self.nodes,
            edges=self.edges,
            file_path=self.file_path,
            file_hash=compute_file_hash(self._abs_file_path) if os.path.exists(self._abs_file_path) else '',
            language=self.pack.language,
        )

    def _visit(self, node):
        """Visit a node and dispatch to appropriate handler."""
        node_type = node.type
        handled = False

        # Go special: type_spec can be struct or interface
        if self.pack.language == 'go' and node_type == 'type_spec':
            self._handle_go_type_spec(node)
            return

        # Rust special: impl_item needs scope tracking
        if self.pack.language == 'rust' and node_type == 'impl_item':
            self._handle_rust_impl(node)
            return

        # Rust special: struct_item → kind='struct' (not 'class')
        if self.pack.language == 'rust' and node_type == 'struct_item':
            self._handle_rust_struct(node)
            handled = True

        # C/C++ special: struct_specifier → kind='struct'
        if self.pack.language in ('c', 'cpp') and node_type == 'struct_specifier':
            self._handle_c_struct(node)
            handled = True

        # C typedef → constant/type alias
        if self.pack.language in ('c', 'cpp') and node_type == 'type_definition':
            self._handle_c_typedef(node)
            # continue recursing to find inner struct_specifier

        # C++ qualified out-of-class method: `void Bar::bye() {...}`
        # Only dispatch if qualifier matches a known class/struct — otherwise it's
        # a namespace-qualified free function and should fall through to _handle_function.
        if self.pack.language == 'cpp' and node_type == 'function_definition':
            decl = node.child_by_field_name('declarator')
            inner = decl
            while inner is not None and inner.type != 'function_declarator':
                inner = inner.child_by_field_name('declarator')
            if inner is not None:
                decl_name = inner.child_by_field_name('declarator')
                if decl_name is not None and decl_name.type == 'qualified_identifier':
                    qname = decl_name.text.decode()
                    class_name, _, _ = qname.rpartition('::')
                    class_simple = class_name.rsplit('::', 1)[-1]
                    if class_simple in self._known_classes:
                        self._handle_cpp_qualified_method(node, decl_name)
                        return

        # C++ alias_declaration: using Foo = int;
        if self.pack.language == 'cpp' and node_type == 'alias_declaration':
            self._handle_cpp_alias(node)
            return

        # Class types
        if node_type in self.pack.class_types and self.pack.extract_class:
            self._handle_class(node)
            handled = True

        # Interface types
        elif node_type in self.pack.interface_types and self.pack.extract_interface:
            self._handle_interface(node)
            handled = True

        # Function types (could be method if inside class/struct/impl)
        elif node_type in self.pack.function_types:
            if self._class_stack:
                self._handle_method_from_function(node)
            else:
                self._handle_function(node)
            handled = True

        # Explicit method types (TS method_definition, Go method_declaration)
        elif node_type in self.pack.method_types:
            self._handle_explicit_method(node)
            handled = True

        # Import types
        elif node_type in self.pack.import_types and self.pack.extract_import:
            self._handle_import(node)
            return  # don't recurse into import children

        # Call types
        elif node_type in self.pack.call_types:
            self._handle_call(node)
            # Continue recursing for nested calls

        # Type alias types
        elif node_type in self.pack.type_alias_types and self.pack.extract_type_alias:
            self._handle_type_alias(node)
            return

        # Constant types (only at module/file level, not inside functions/methods)
        elif node_type in self.pack.constant_types and self.pack.extract_constant and not self._class_stack:
            # Check parent is module/program level (not inside a function body)
            parent = node.parent
            is_top_level = parent and parent.type in (
                'module', 'program', 'source_file',  # Python, TS/JS, Go/Rust/Java
                'export_statement',  # TS export const
                'translation_unit',  # C/C++ root
                'declaration_list',  # C++ namespace body
                'linkage_specification',  # extern "C" { ... }
            )
            if is_top_level:
                self._handle_constant(node)

        # Module types
        elif node_type in self.pack.module_types and self.pack.extract_module:
            self._handle_module(node)

        # Recurse into children (skip nodes that handle their own recursion)
        if not handled:
            for child in node.children:
                self._visit(child)

    def _handle_class(self, node):
        info = self.pack.extract_class(node)
        name = info.get('name', '')
        if not name:
            return

        visibility = 'public'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        node_id = make_node_id('class', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='class',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"class {name}",
            language=self.pack.language,
            visibility=visibility,
        )
        self.nodes.append(code_node)
        self._known_classes[name] = 'class'

        # defines edge
        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

        # extends edges
        for base in info.get('bases', []):
            base_simple = base.rsplit('::', 1)[-1]
            base_kind = self._known_classes.get(base_simple, 'class')
            self.edges.append(CodeEdge(
                from_id=node_id,
                to_id=f"{base_kind}.{base}",
                kind='extends',
                line_number=node.start_point[0] + 1,
                confidence=0.8,
            ))

        # Recurse into class body with class context
        self._class_stack.append((name, 'class'))
        for child in node.children:
            self._visit(child)
        self._class_stack.pop()

    def _handle_interface(self, node):
        info = self.pack.extract_interface(node)
        name = info.get('name', '')
        if not name:
            return

        visibility = 'public'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        node_id = make_node_id('interface', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='interface',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"interface {name}",
            language=self.pack.language,
            visibility=visibility,
        )
        self.nodes.append(code_node)

        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

        for base in info.get('bases', []):
            self.edges.append(CodeEdge(
                from_id=node_id,
                to_id=f"interface.{base}",
                kind='extends',
                line_number=node.start_point[0] + 1,
                confidence=0.8,
            ))

        # Java 等語言：interface 方法是 API 契約，需以 interface 名作 scope 提取
        # （先前 interface 子節點完全不走訪 → Java interface 方法以無 scope 流出，
        #   實測在真實專案造成大量假 drift）
        if self.pack.interface_members_scoped:
            self._class_stack.append((name, 'interface'))
            for child in node.children:
                self._visit(child)
            self._class_stack.pop()

    def _handle_function(self, node):
        if not self.pack.extract_function:
            return
        info = self.pack.extract_function(node)
        name = info.get('name', '')
        if not name:
            return

        visibility = 'public'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        sig = info.get('signature', '()')
        node_id = make_node_id('function', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='function',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"{'async ' if info.get('is_async') else ''}def {name}{sig}",
            language=self.pack.language,
            visibility=visibility,
        )
        self.nodes.append(code_node)

        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

        # Recurse into function body for calls
        for child in node.children:
            self._visit(child)

    def _handle_method_from_function(self, node):
        """Handle function_definition inside a class (Python/Java/Rust)."""
        if not self.pack.extract_function:
            return
        info = self.pack.extract_function(node)
        name = info.get('name', '')
        if not name:
            return

        class_name, class_kind = self._class_stack[-1] if self._class_stack else (self._impl_type or '', 'class')
        visibility = 'public'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        sig = info.get('signature', '()')
        node_id = make_node_id('function', self.file_path, f"{class_name}.{name}")
        code_node = CodeNode(
            id=node_id,
            kind='function',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"{'async ' if info.get('is_async') else ''}def {name}{sig}",
            language=self.pack.language,
            visibility=visibility,
        )
        self.nodes.append(code_node)

        # contains edge from class/struct
        if class_name:
            self.edges.append(CodeEdge(
                from_id=make_node_id(class_kind, self.file_path, class_name),
                to_id=node_id,
                kind='contains',
                line_number=node.start_point[0] + 1,
            ))

        # Recurse into method body for calls
        for child in node.children:
            self._visit(child)

    def _handle_explicit_method(self, node):
        """Handle TS method_definition or Go method_declaration."""
        if self.pack.extract_method:
            info = self.pack.extract_method(node)
        elif self.pack.extract_function:
            info = self.pack.extract_function(node)
        else:
            return

        name = info.get('name', '')
        if not name:
            return

        # Determine parent class
        class_name = self._class_stack[-1][0] if self._class_stack else ''
        class_kind = self._class_stack[-1][1] if self._class_stack else 'class'
        if not class_name and info.get('receiver_type'):
            class_name = info['receiver_type']

        visibility = 'public'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        sig = info.get('signature', '()')
        qualified = f"{class_name}.{name}" if class_name else name
        node_id = make_node_id('function', self.file_path, qualified)
        code_node = CodeNode(
            id=node_id,
            kind='function',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"{'async ' if info.get('is_async') else ''}{name}{sig}",
            language=self.pack.language,
            visibility=visibility,
        )
        self.nodes.append(code_node)

        if class_name:
            self.edges.append(CodeEdge(
                from_id=make_node_id(class_kind, self.file_path, class_name),
                to_id=node_id,
                kind='contains',
                line_number=node.start_point[0] + 1,
            ))

        # Recurse for calls
        for child in node.children:
            self._visit(child)

    def _handle_import(self, node):
        info = self.pack.extract_import(node)
        module = info.get('module', '')
        text = info.get('text', '')

        if module:
            self.edges.append(CodeEdge(
                from_id=make_node_id('file', self.file_path),
                to_id=f"module.{module}",
                kind='imports',
                line_number=node.start_point[0] + 1,
                confidence=0.9,
            ))
        for name in info.get('names', []):
            if name and name != module:
                self.edges.append(CodeEdge(
                    from_id=make_node_id('file', self.file_path),
                    to_id=f"module.{name}",
                    kind='imports',
                    line_number=node.start_point[0] + 1,
                    confidence=0.9,
                ))

    def _handle_call(self, node):
        func_node = node.child_by_field_name('function')
        if not func_node:
            return
        target = func_node.text.decode()

        # Determine caller context
        caller_id = make_node_id('file', self.file_path)
        parent = node.parent
        while parent:
            if parent.type in (self.pack.function_types + self.pack.method_types):
                if self.pack.language in ('c', 'cpp'):
                    decl = parent.child_by_field_name('declarator')
                    raw = _c_declarator_name(decl) if decl else ''
                    fname_text = raw.split('::')[-1] if raw else ''
                else:
                    fname_node = parent.child_by_field_name('name')
                    fname_text = fname_node.text.decode() if fname_node else ''
                if fname_text:
                    if self._class_stack:
                        class_name = self._class_stack[-1][0]
                        caller_id = make_node_id('function', self.file_path, f"{class_name}.{fname_text}")
                    else:
                        caller_id = make_node_id('function', self.file_path, fname_text)
                break
            parent = parent.parent

        confidence = 1.0 if '.' not in target else 0.9
        self.edges.append(CodeEdge(
            from_id=caller_id,
            to_id=f"symbol.{target}",
            kind='calls',
            line_number=node.start_point[0] + 1,
            confidence=confidence,
        ))

    def _handle_type_alias(self, node):
        info = self.pack.extract_type_alias(node)
        name = info.get('name', '')
        if not name:
            return

        node_id = make_node_id('type', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='type',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=self.pack.language,
            visibility='public',
        )
        self.nodes.append(code_node)

        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

    def _handle_constant(self, node):
        info = self.pack.extract_constant(node)
        if not info:
            return

        name = info['name']

        # TS: const arrow functions → treat as function
        if info.get('is_function'):
            node_id = make_node_id('function', self.file_path, name)
            code_node = CodeNode(
                id=node_id,
                kind='function',
                name=name,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                language=self.pack.language,
                visibility='public',
            )
            self.nodes.append(code_node)
            self.edges.append(CodeEdge(
                from_id=make_node_id('file', self.file_path),
                to_id=node_id,
                kind='defines',
                line_number=node.start_point[0] + 1,
            ))
            return

        node_id = make_node_id('constant', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='constant',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=self.pack.language,
            visibility='public',
        )
        self.nodes.append(code_node)
        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

    def _handle_module(self, node):
        if not self.pack.extract_module:
            return
        info = self.pack.extract_module(node)
        name = info.get('name', '')
        if not name:
            return

        node_id = make_node_id('module', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='module',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            language=self.pack.language,
        )
        self.nodes.append(code_node)

    def _handle_go_type_spec(self, node):
        """Go-specific: type_spec can be struct or interface."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return

        name = name_node.text.decode()
        # Determine if it's a struct or interface by looking at the type child
        type_child = node.child_by_field_name('type')
        if not type_child:
            return

        visibility = 'public' if name[0].isupper() else 'private'

        if type_child.type == 'interface_type':
            node_id = make_node_id('interface', self.file_path, name)
            code_node = CodeNode(
                id=node_id,
                kind='interface',
                name=name,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"interface {name}",
                language='go',
                visibility=visibility,
            )
            self.nodes.append(code_node)
            self.edges.append(CodeEdge(
                from_id=make_node_id('file', self.file_path),
                to_id=node_id,
                kind='defines',
                line_number=node.start_point[0] + 1,
            ))
        elif type_child.type == 'struct_type':
            node_id = make_node_id('class', self.file_path, name)
            code_node = CodeNode(
                id=node_id,
                kind='class',
                name=name,
                file_path=self.file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                signature=f"struct {name}",
                language='go',
                visibility=visibility,
            )
            self.nodes.append(code_node)
            self.edges.append(CodeEdge(
                from_id=make_node_id('file', self.file_path),
                to_id=node_id,
                kind='defines',
                line_number=node.start_point[0] + 1,
            ))

    def _handle_rust_struct(self, node):
        """Rust struct_item → kind='struct' for compatibility with regex backend."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return
        name = name_node.text.decode()
        visibility = 'private'
        if self.pack.detect_visibility:
            visibility = self.pack.detect_visibility(name, node=node)

        node_id = make_node_id('struct', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='struct',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"struct {name}",
            language='rust',
            visibility=visibility,
        )
        self.nodes.append(code_node)

        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

    def _handle_c_struct(self, node):
        """C/C++ struct_specifier → kind='struct'. Skip anonymous or forward decls."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return  # anonymous struct
        # Skip forward declarations (no body)
        body = node.child_by_field_name('body')
        if not body:
            return
        name = name_node.text.decode()
        node_id = make_node_id('struct', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='struct',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"struct {name}",
            language=self.pack.language,
            visibility='public',
        )
        self.nodes.append(code_node)
        self._known_classes[name] = 'struct'
        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))
        # C++ struct supports inheritance — emit extends edges
        if self.pack.language == 'cpp':
            for child in node.children:
                if child.type == 'base_class_clause':
                    for c in child.named_children:
                        if c.type in ('type_identifier', 'qualified_identifier', 'template_type'):
                            base_name = c.text.decode()
                            base_simple = base_name.rsplit('::', 1)[-1]
                            base_kind = self._known_classes.get(base_simple, 'class')
                            self.edges.append(CodeEdge(
                                from_id=node_id,
                                to_id=f"{base_kind}.{base_name}",
                                kind='extends',
                                line_number=node.start_point[0] + 1,
                                confidence=0.8,
                            ))
            # Recurse into struct body with struct context (for C++ methods)
            self._class_stack.append((name, 'struct'))
            for child in node.children:
                self._visit(child)
            self._class_stack.pop()

    def _handle_cpp_qualified_method(self, node, qualified_name_node):
        """C++ out-of-class method: `void Bar::bye() {...}` → method under class Bar."""
        qname = qualified_name_node.text.decode()
        if '::' not in qname:
            return
        class_name, _, method_name = qname.rpartition('::')
        # Drop leading `namespace::` if multiple — keep last qualifier as class
        class_simple = class_name.rsplit('::', 1)[-1]

        sig = '()'
        # find function_declarator parameters
        decl = node.child_by_field_name('declarator')
        inner = decl
        while inner is not None and inner.type != 'function_declarator':
            inner = inner.child_by_field_name('declarator')
        if inner is not None:
            params = inner.child_by_field_name('parameters')
            if params:
                sig = params.text.decode()

        container_kind = self._known_classes.get(class_simple, 'class')
        node_id = make_node_id('function', self.file_path, f"{class_simple}.{method_name}")
        code_node = CodeNode(
            id=node_id,
            kind='function',
            name=method_name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"{class_simple}::{method_name}{sig}",
            language='cpp',
            visibility='public',
        )
        self.nodes.append(code_node)
        # contains edge from class/struct → method
        self.edges.append(CodeEdge(
            from_id=make_node_id(container_kind, self.file_path, class_simple),
            to_id=node_id,
            kind='contains',
            line_number=node.start_point[0] + 1,
            confidence=0.9,
        ))
        # Push container context for call graph attribution
        self._class_stack.append((class_simple, container_kind))
        for child in node.children:
            self._visit(child)
        self._class_stack.pop()

    def _handle_cpp_alias(self, node):
        """C++ alias_declaration: `using Foo = int;` → kind='type'."""
        name_node = node.child_by_field_name('name')
        if not name_node:
            return
        name = name_node.text.decode()
        node_id = make_node_id('type', self.file_path, name)
        code_node = CodeNode(
            id=node_id,
            kind='type',
            name=name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"using {name}",
            language='cpp',
            visibility='public',
        )
        self.nodes.append(code_node)
        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

    def _handle_c_typedef(self, node):
        """C/C++ typedef → emit a type node; inner struct is handled separately."""
        # typedef struct Foo {...} FooAlias;  → emit alias as kind='type'
        # The last identifier child is the alias name
        alias_name = None
        for child in reversed(node.named_children):
            if child.type == 'type_identifier':
                alias_name = child.text.decode()
                break
        if not alias_name:
            return
        node_id = make_node_id('type', self.file_path, alias_name)
        code_node = CodeNode(
            id=node_id,
            kind='type',
            name=alias_name,
            file_path=self.file_path,
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            signature=f"typedef {alias_name}",
            language=self.pack.language,
            visibility='public',
        )
        self.nodes.append(code_node)
        self.edges.append(CodeEdge(
            from_id=make_node_id('file', self.file_path),
            to_id=node_id,
            kind='defines',
            line_number=node.start_point[0] + 1,
        ))

    def _handle_rust_impl(self, node):
        """Rust impl block: track type for method association."""
        # impl [Trait for] Type { ... }
        type_node = node.child_by_field_name('type')
        trait_node = node.child_by_field_name('trait')

        type_name = type_node.text.decode() if type_node else ''
        if trait_node:
            trait_name = trait_node.text.decode()
            # Create implements edge
            if type_name:
                self.edges.append(CodeEdge(
                    from_id=make_node_id('struct', self.file_path, type_name),
                    to_id=f"interface.{trait_name}",
                    kind='implements',
                    line_number=node.start_point[0] + 1,
                    confidence=0.9,
                ))

        # Set impl context and recurse
        old_impl = self._impl_type
        self._impl_type = type_name
        self._class_stack.append((type_name, 'struct'))
        for child in node.children:
            self._visit(child)
        self._class_stack.pop()
        self._impl_type = old_impl


# =============================================================================
# TreeSitterBackend
# =============================================================================

class TreeSitterBackend:
    """Tree-sitter AST-based extraction backend (high fidelity)."""

    @property
    def name(self) -> str:
        return 'tree_sitter'

    @property
    def capabilities(self) -> Set[str]:
        return {'functions', 'classes', 'imports', 'methods', 'calls'}

    def can_handle(self, language: str) -> bool:
        # Check if we *support* this language (have a query pack + loader spec).
        # Don't require grammar to be loaded — it will be auto-installed on extract().
        # This ensures the registry selects TreeSitterBackend even when grammars
        # aren't yet installed, allowing extract_language() to trigger auto-install.
        return language in QUERY_PACKS and language in _LOADER_MAP

    def extract(self, content: str, file_path: str, abs_file_path: str = None) -> ExtractionResult:
        """Extract using auto-detected language from file extension."""
        from tools.code_graph_extractor.extractor import detect_language
        language = detect_language(file_path)
        if not language:
            return ExtractionResult(
                file_path=file_path,
                errors=[f"TreeSitterBackend: cannot detect language for {file_path}"]
            )
        return self.extract_language(content, file_path, language, abs_file_path=abs_file_path)

    def extract_language(self, content: str, file_path: str, language: str, abs_file_path: str = None) -> ExtractionResult:
        """Extract with explicit language."""
        grammar = _load_grammar(language)
        if not grammar:
            return ExtractionResult(
                file_path=file_path,
                language=language,
                errors=[f"TreeSitterBackend: grammar not available for {language}"]
            )

        pack = QUERY_PACKS.get(language)
        if not pack:
            return ExtractionResult(
                file_path=file_path,
                language=language,
                errors=[f"TreeSitterBackend: no query pack for {language}"]
            )

        parser = tree_sitter.Parser(grammar)
        tree = parser.parse(content.encode('utf-8'))

        walker = ASTWalker(pack, file_path, abs_file_path=abs_file_path)
        return walker.walk(tree.root_node)

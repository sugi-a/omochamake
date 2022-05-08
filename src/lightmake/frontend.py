import sys, os, pathlib, re, abc, contextlib, collections
from collections import namedtuple

from .utils import map_nested, flatten_nested, get_deep
from .core.decls import NOP, Rule
from .core.make import make
from .core import misc
from .core.make_mt import make_multi_thread
from .writer.writer import get_default_writer, HTMLWriter, Writer

_default_writer = get_default_writer()

class Self:
    def __getitem__(self, k):
        if not isinstance(k, (int, str)):
            raise ValueError(f'Key must be int or str. Given {k}')

        return Self((*self._subname, k))

    def get_subname(self):
        return self._subname

    def __getattr__(self, k):
        return self[k]

    def __init__(self, subname):
        self._subname = subname

    def __repr__(self):
        def f(n):
            if isinstance(n, str) and n.isidentifier(): return '.' + n
            else: return f'[{repr(n)}]'
        return 'SELF' +  ''.join(map(f, self._subname))

SELF = Self(())

def create_group(path_prefix):
    return Group(None, path_prefix, ())


class Group:
    # APIs
    def add(self, name, path, method, *args, **kwargs):
        if not isinstance(name, (str, int)):
            raise ValueError(f'name must be str or int')

        if name in self._children:
            raise KeyError(f'name `{name}` already exists')

        if not callable(method) and method is not NOP:
            raise ValueError('method must be a callable')

        path = map_nested(path, lambda p: _add_pfx_to_simple_path(self._path_prefix, p))

        realpaths = set(map(os.path.realpath, flatten_nested(path)))
        for p in realpaths:
            if p in self._root._path_str_set:
                raise ValueError(f'path {p} is already used by another rule')

        trg = create_rule(self._root, path, (*self._name, name), method, args, kwargs)

        trg_ptr = _create_rule_wrapper(self._root, trg, path)

        self._root._path_str_set.update(realpaths)

        self._children[name] = trg_ptr

        if _ismembername(name):
            self.__dict__[name] = trg_ptr

        return trg_ptr

    def adder(self, path, *args,**kwargs):
        def adder(method):
            return self.add(method.__name__, path, method, *args, **kwargs)
        
        return adder

    def add_readonly(self, name, path):
        self.add(name, path, NOP)


    def add_group(self, name, path_prefix):
        if not isinstance(name, (str, int)):
            raise ValueError('name must be str or int') # ToDo

        if name in self._children:
            raise KeyError(f'name {repr(name)} already exists in this Group')

        if isinstance(path_prefix, str):
            path_prefix = os.path.join(self._path_prefix, path_prefix)
        elif isinstance(path_prefix, NoPfxPath):
            path_prefix = path_prefix.path
        else:
            raise ValueError('path_prefix must be str or NoPfxPath')

        r = Group(self._root, path_prefix, (*self._name, name))

        self._children[name] = r

        if _ismembername(name):
            self.__dict__[name] = r

        return r


    def make(self, dry_run=False, stop_on_fail=False, logfile=None, nthreads=1):
        trgs = []
        stack = [self]

        while stack:
            a = stack.pop()
            if isinstance(a, Group):
                for c in a._children.values():
                    stack.append(c)
            else:
                trgs.append(a._get_rule())

        _make_wrapper(trgs, dry_run, stop_on_fail, logfile, nthreads)

    def clean(self):
        for c in self._children.values():
            c.clean()

    def __repr__(self):
        name = repr_group_name(self._name)
        return f'<Group name={repr(name)} path_prefix={repr(self._path_prefix)}>'

    def __getitem__(self, k):
        return self._children[k]

    def __init__(self, root, path_prefix, name):
        if root is None:
            self._root = self
            self._path_str_set = set()
        else:
            self._root = root

        self._path_prefix = path_prefix
        self._name = name
        self._children = {}


class ITarget(abc.ABC):
    @abc.abstractmethod
    def path(self):
        pass

    def _get_rule(self):
        return self._rule

    def _get_root(self):
        return self._root

    def __init__(self, root, rule):
        self._rule = rule
        self._root = root


class IRuleWrapper(ITarget):
    def make(self, dry_run=False, stop_on_fail=False, logfile=None, nthreads=1):
        _make_wrapper([self._rule], dry_run, stop_on_fail, logfile, nthreads)

    def clean(self):
        misc.clean([self._rule], _default_writer)


class FrozenMap(collections.abc.Mapping):
    def __init__(self, mapping):
        if not isinstance(mapping, dict):
            if isinstance(mapping, collections.abc.Mapping):
                mapping = dict(mapping)
            else:
                raise Exception('Internal error: mapping must be dict|Mapping')

        for k,v in mapping.items():
            if _ismembername(k):
                setattr(self, k, v)

        self._mapping = mapping

    def __len__(self): return self._mapping.__len__()
    def __iter__(self): return self._mapping.__iter__()
    def __getitem__(self, k): return self._mapping.__getitem__(k)
    def __repr__(self): return self._mapping.__repr__()
    

class TargetDict(FrozenMap, ITarget):
    def path(self):
        return {k: v.path() for k,v in self.items()}

    def __init__(self, root, rule, target_map):
        ITarget.__init__(self, root, rule)
        FrozenMap.__init__(self, target_map)


class RuleWrapperDict(TargetDict, IRuleWrapper):
    pass


class TargetTuple(tuple, ITarget):
    def path(self):
        return tuple(c.path() for c in self)

    def __init__(self, root, rule, lst):
        ITarget.__init__(self, root, rule)

    def __new__(cls, root, rule, lst):
        return super().__new__(cls, lst)

class RuleWrapperTuple(TargetTuple, IRuleWrapper):
    pass


class TargetSimple(ITarget):
    def path(self):
        return self._path

    def __init__(self, root, rule, path_str):
        super().__init__(root, rule)
        self._path = path_str

    def __repr__(self):
        return f'<{self.__class__.__name__} path={repr(self._path)}>'


class RuleWrapperSimple(TargetSimple, IRuleWrapper):
    pass


def create_rule(root, path, name, method, args, kwargs):
    depset = set()
    has_self = False
    self_paths = set()
    dep_paths = set()

    if len(flatten_nested(path)) == 0:
        raise ValueError('path must contain at least one str')

    def map_fn(arg):
        nonlocal has_self

        if isinstance(arg, TargetSimple):
            dep_rule = arg._get_rule()
            dep_root = arg._get_root()
            p = arg.path()

            if dep_root is not root:
                raise ValueError(f'Rule may not depend on Rules under different root Group')

            depset.add(dep_rule)
            dep_paths.add(p)
            return p

        if isinstance(arg, Self):
            try:
                p = get_deep(path, arg.get_subname())
            except:
                raise ValueError(f'Invalid keys for SELF')

            has_self = True
            self_paths.add(p)
            return p

        return arg

    args, kwargs = map_nested((args, kwargs), map_fn)

    if has_self:
        # check if all path-strs in path are used in args/kwargs
        for p in flatten_nested(path):
            if p not in self_paths:
                #_default_writer(f'WARNING: {repr(p)} will not be passed to the method\n', logkind='warning')
                raise ValueError(f'{repr(p)} is not be passed to the method')
    else:
        args = (path, *args)
        self_paths = set(flatten_nested(path))


    return Rule(repr_rule_name(name), method, args, kwargs, depset, self_paths, dep_paths)


def _create_rule_wrapper(root, rule, path):
    if isinstance(path, (dict, collections.abc.Mapping)):
        trgs = { k: _create_target_pointer(root, rule, v) for k,v in path.items() }
        return RuleWrapperDict(root, rule, trgs)
    elif isinstance(path, (list, tuple)):
        trgs = tuple(_create_target_pointer(root, rule, v) for v in path)
        return RuleWrapperTuple(root, rule, trgs)
    elif isinstance(path, str):
        return RuleWrapperSimple(root, rule, path)
    else:
        raise TypeError(f'Nested path must be dict|tuple|list|str')


def _create_target_pointer(root, rule, path):
    def _mapping(mapping):
        return TargetDict(root, rule, mapping)

    def _tuple(lst):
        return TargetTuple(root, rule, lst)

    def map_fn(path_str):
        if not isinstance(path_str, str):
            raise ValueError(f'Fatal error. path_str must be str. given {path_str}')

        return TargetSimple(root, rule, path_str)

    return map_nested(
        path, map_fn,
        _seq_src_type_dst_factory_pairs=[((list, tuple), _tuple)],
        _map_src_type_dst_factory_pairs=[(dict, _mapping)]
    )
    

class NoPfxPath:
    def __init__(self, path):
        self.path = path


def nopfx(path):
    def func(path_str):
        if not isinstance(path_str, str):
            raise ValueError(f'Constituent of nested path must be str|tuple|list|dict. Given: {path}')
        return NoPfxPath(path_str)

    return map_nested(path, func)


def _add_pfx_to_simple_path(path_prefix, simple_path):
    if isinstance(simple_path, str):
        if os.path.isabs(simple_path):
            return simple_path
        else:
            return f'{path_prefix}{simple_path}'
    elif isinstance(simple_path, NoPfxPath):
        return simple_path.path
    else:
        raise TypeError(f'Terminal element of a nested path must be str or NoPfxPath. Given {simple_path}')


def _make_wrapper(trgs, dry_run, stop_on_fail, logfile, nthreads):
    with contextlib.ExitStack() as s:
        if logfile is None:
            writer = _default_writer
        else:
            f = s.enter_context(open(logfile, 'w'))
            if logfile[-5:] == '.html':
                writer = s.enter_context(HTMLWriter(f))
            else:
                writer = Writer(f)

        if nthreads >= 2:
            make_multi_thread(trgs, dry_run, stop_on_fail, writer, nthreads)
        else:
            make(trgs, dry_run, stop_on_fail, writer)


def repr_group_name(group_name):
    return '/' + '/'.join(map(str, group_name))

def repr_rule_name(trg_name):
    return repr_group_name(trg_name[:-1]) + ':' + str(trg_name[-1])

def _ismembername(name):
    return isinstance(name, str) and name.isidentifier() and name[0] != '_'
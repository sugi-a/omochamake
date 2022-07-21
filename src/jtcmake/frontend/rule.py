from typing import Callable, Any, Sequence, Union
import sys, os, hashlib, json
from pathlib import Path, PurePath

from ..core.rule import Event, IRule
from .file import IFile, IVFile

class Rule(IRule):
    def __init__(
        self,
        name: Sequence[str],
        yfiles: list[IFile],
        xfiles: list[tuple[tuple, IFile]],
        deplist: list[IRule],
        method,
        args,
        kwargs,
    ):
        self.name = name
        self.yfiles = yfiles
        self.xfiles = xfiles
        self._deplist = deplist
        self._method = method
        self._args = args
        self._kwargs = kwargs


    def should_update(
        self,
        updated_rules: set[IRule],
        dry_run: bool
    ) -> bool:
        for k,f in self.xfiles:
            if not os.path.exists(f.path):
                if dry_run:
                    return True
                else:
                    raise Exception(f'Input file {f.path} is missing')

            if os.path.getmtime(f.path) == 0:
                if dry_run:
                    return True
                else:
                    raise Exception(
                        f'Input file {f.path} has mtime of 0. '
                        f'Input files with mtime of 0 are considered to be '
                        f'invalid for reducing operational error.'
                    )


        if dry_run and any(r in updated_rules for r in self._deplist):
            return True

        if any(not os.path.exists(f.path) for f in self.yfiles):
            return True

        oldest_y = min(os.path.getmtime(f.path) for f in self.yfiles)

        if oldest_y <= 0:
            return True

        xvfiles = [] # input VFiles that are updated

        for k,f in self.xfiles:
            if os.path.getmtime(f.path) > oldest_y:
                if isinstance(f, IVFile):
                    xvfiles.append((k,f))
                else:
                    return True

        if len(xvfiles) > 0:
            hash_dic = \
                {tuple(k): (h,t) for k,h,t in load_vfile_hashes(self.metadata_fname)}
                
            for k,f in xvfiles:
                if k not in hash_dic:
                    return True

                mtime = os.path.getmtime(f.path)
                hash_, mtime_ = hash_dic[k]

                # Optimization: skip computing hash if the current mtime
                # is equal to the one in the cache
                if mtime != mtime_ and f.get_hash() != hash_:
                    return True

        return False


    def preprocess(self, callback: Callable[[Event], None]):
        for f in self.yfiles:
            try:
                os.makedirs(os.path.dirname(f.path), exist_ok=True)
            except:
                pass


    def postprocess(self, callback: Callable[[Event], None], succ: bool):
        if succ:
            self.update_xvfile_hashes()
        else:
            # set mtime to 0
            for f in self.yfiles:
                os.utime(f.path, (0, 0))

            # delete vfile cache
            try:
                os.remove(self.metadata_fname)
            except:
                pass


    @property
    def metadata_fname(self):
        p = PurePath(self.yfiles[0].path)
        return p.parent / '.jtcmake' / p.name


    def update_xvfile_hashes(self):
        xvfiles = [(k,v) for k,v in self.xfiles if isinstance(v, IVFile)]
        if len(xvfiles) > 0:
            save_vfile_hashes(self.metadata_fname, xvfiles)


    @property
    def method(self) -> Callable: return self._method

    @property
    def args(self) -> tuple[Any]: return self._args

    @property
    def kwargs(self) -> dict[Any, Any]: return self._kwargs

    @property
    def deplist(self) -> list[IRule]: return self._deplist


def create_vfile_hashes(vfiles) -> list[tuple[tuple, str, float]]:
    """
    list of (DeepKey, file name, mtime)
    """
    res = [(k, f.get_hash(), os.path.getmtime(f.path)) for k,f in vfiles]
    res = json.loads(json.dumps(res)) # round trip JSON conversion
    return res
    

def save_vfile_hashes(metadata_fname, vfiles):
    hashes = create_vfile_hashes(vfiles)
    os.makedirs(os.path.dirname(metadata_fname), exist_ok=True)
    with open(metadata_fname, 'w') as f:
        try:
            json.dump(hashes, f)
        except e:
            raise Exception(
                f'Failed to save IVFile hashes as JSON to {metadata_fname}.'
                f'This may be because some dictionary keys in the arguments'
                f' to specify the IVFile objects are not JSON convertible.'
            ) from e


def load_vfile_hashes(metadata_fname):
    if not os.path.exists(metadata_fname):
        return []

    with open(metadata_fname) as f:
        return json.load(f)


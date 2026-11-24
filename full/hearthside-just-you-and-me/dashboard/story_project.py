"""Read-only Hearthside documents for the requesting Hermes profile.

No folder grants, client-supplied paths, identity changes or writes on GET.
"""
from __future__ import annotations
import json
import os
import stat
import sys
from pathlib import Path
from fastapi import APIRouter,HTTPException,Response
from hermes_constants import get_hermes_home

router=APIRouter()
OWNER='hearthside-just-you-and-me'
DOCUMENTS=frozenset({'SOUL.md','Nori.md','Room.md','Outfits.md','Journal.md'})
LIMIT=4*1024*1024
MARKER='.hearthside-project.json'

def _windows_fd(root:Path,parts:tuple[str,...])->int:
    """Anchor to the trusted home handle; refuse reparses throughout the relative path.

    Uses only the standard library. NtCreateFile's OBJ_DONT_REPARSE covers every
    untrusted component, unlike a path stat followed by a normal Windows open.
    The root is canonicalized by _source before this call, as in the POSIX path.
    """
    import ctypes as ct
    import msvcrt
    class UnicodeString(ct.Structure):
        _fields_=[('Length',ct.c_uint16),('MaximumLength',ct.c_uint16),('Buffer',ct.c_wchar_p)]
    class ObjectAttributes(ct.Structure):
        _fields_=[('Length',ct.c_uint32),('RootDirectory',ct.c_void_p),('ObjectName',ct.POINTER(UnicodeString)),('Attributes',ct.c_uint32),('SecurityDescriptor',ct.c_void_p),('SecurityQualityOfService',ct.c_void_p)]
    class StatusValue(ct.Union):
        _fields_=[('Status',ct.c_int32),('Pointer',ct.c_void_p)]
    class IoStatus(ct.Structure):
        _fields_=[('Value',StatusValue),('Information',ct.c_size_t)]
    class AttributeTag(ct.Structure):
        _fields_=[('FileAttributes',ct.c_uint32),('ReparseTag',ct.c_uint32)]
    kernel=ct.WinDLL('kernel32',use_last_error=True);nt=ct.WinDLL('ntdll',use_last_error=True)
    kernel.CreateFileW.argtypes=[ct.c_wchar_p,ct.c_uint32,ct.c_uint32,ct.c_void_p,ct.c_uint32,ct.c_uint32,ct.c_void_p];kernel.CreateFileW.restype=ct.c_void_p
    kernel.GetFileInformationByHandleEx.argtypes=[ct.c_void_p,ct.c_int,ct.c_void_p,ct.c_uint32];kernel.GetFileInformationByHandleEx.restype=ct.c_int
    kernel.CloseHandle.argtypes=[ct.c_void_p];kernel.CloseHandle.restype=ct.c_int
    nt.NtCreateFile.argtypes=[ct.POINTER(ct.c_void_p),ct.c_uint32,ct.POINTER(ObjectAttributes),ct.POINTER(IoStatus),ct.c_void_p,ct.c_uint32,ct.c_uint32,ct.c_uint32,ct.c_uint32,ct.c_void_p,ct.c_uint32];nt.NtCreateFile.restype=ct.c_int32
    nt.RtlNtStatusToDosError.argtypes=[ct.c_int32];nt.RtlNtStatusToDosError.restype=ct.c_uint32
    def attributes(handle):
        info=AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle,9,ct.byref(info),ct.sizeof(info)):raise ct.WinError(ct.get_last_error())
        if info.FileAttributes&0x400:raise PermissionError('Project reparse point refused')
        return info.FileAttributes
    root_name=str(root)
    if not root_name.startswith('\\\\?\\'):
        root_name='\\\\?\\UNC\\'+root_name[2:]if root_name.startswith('\\\\')else'\\\\?\\'+root_name
    root_handle=kernel.CreateFileW(root_name,0x001000A0,7,None,3,0x02200000,None)
    if root_handle in(None,ct.c_void_p(-1).value):raise ct.WinError(ct.get_last_error())
    child=ct.c_void_p()
    try:
        if not attributes(root_handle)&0x10:raise PermissionError('Invalid project root')
        name='\\'.join(parts);length=len(name.encode('utf-16-le'))
        if length>65532:raise ValueError('Project path too long')
        buffer=ct.create_unicode_buffer(name);unicode_name=UnicodeString(length,length+2,ct.cast(buffer,ct.c_wchar_p))
        # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE; a root-relative name cannot escape.
        obj=ObjectAttributes(ct.sizeof(ObjectAttributes),root_handle,ct.pointer(unicode_name),0x1040,None,None)
        status=IoStatus()
        # FILE_GENERIC_READ; FILE_OPEN; NON_DIRECTORY | SYNCHRONOUS_IO_NONALERT |
        # OPEN_REPARSE_POINT. Share read/write/delete to allow atomic editor saves.
        result=nt.NtCreateFile(ct.byref(child),0x00120089,ct.byref(obj),ct.byref(status),None,0,7,1,0x00200060,None,0)
        if result<0:raise ct.WinError(nt.RtlNtStatusToDosError(result))
        if attributes(child.value)&0x10:raise PermissionError('Not a regular project document')
        fd=msvcrt.open_osfhandle(child.value,os.O_RDONLY|getattr(os,'O_BINARY',0))
        child.value=None  # Ownership transferred to the CRT file descriptor.
        return fd
    finally:
        if child.value is not None:kernel.CloseHandle(child.value)
        kernel.CloseHandle(root_handle)

def _open(root:Path,parts:tuple[str,...])->tuple[bytes,os.stat_result]:
    """Readonly, handle-anchored access; no symlinks, reparses or oversized data."""
    if not parts or any(not isinstance(p,str)or p in('','.','..')or any(c in p for c in('/','\\',':','\0'))for p in parts):raise HTTPException(403,'Invalid project document path')
    fd=None;final=None
    try:
        if sys.platform=='win32':final=_windows_fd(root,parts)
        else:
            if not hasattr(os,'O_NOFOLLOW'):raise HTTPException(503,'Secure project file access unavailable')
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for part in parts[:-1]:
                next_fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=next_fd
            final=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        info=os.fstat(final)
        if not stat.S_ISREG(info.st_mode):raise HTTPException(403,'Not a regular project document')
        if info.st_size>LIMIT:raise HTTPException(413,'Project document exceeds 4 MiB')
        chunks=[];length=0
        while True:
            chunk=os.read(final,min(65536,LIMIT+1-length))
            if not chunk:break
            chunks.append(chunk);length+=len(chunk)
            if length>LIMIT:raise HTTPException(413,'Project document exceeds 4 MiB')
        return b''.join(chunks),info
    except FileNotFoundError:raise HTTPException(404,'Hearthside document not found')from None
    except (OSError,ValueError):raise HTTPException(403,'Hearthside document access refused')from None
    finally:
        if final is not None:os.close(final)
        if fd is not None:os.close(fd)

def _json(root:Path,parts:tuple[str,...])->dict:
    data,_=_open(root,parts)
    try:
        result=json.loads(data)
        if not isinstance(result,dict):raise ValueError()
        return result
    except (ValueError,UnicodeError):raise HTTPException(409,'Invalid Hearthside project metadata')from None

def _source()->tuple[Path,tuple[str,...],str]:
    home=get_hermes_home().resolve()
    relative=('workspace','Hearthside')
    try:
        marker=_json(home,relative+(MARKER,))
    except HTTPException as exc:
        if exc.status_code!=404:raise
    else:
        if marker.get('owner')!=OWNER or marker.get('schema')!=1:raise HTTPException(409,'Hearthside project ownership mismatch')
        return home,relative,'project'
    raise HTTPException(404,'Hearthside project is not configured for this Bot')

@router.get('/story/source')
def story_source(response:Response):
    _,_,mode=_source();response.headers['Cache-Control']='no-store'
    return {'status':'ready','mode':mode,'name':'Hearthside','documents':sorted(DOCUMENTS)}

@router.get('/story/documents/{filename}')
def story_document(filename:str,response:Response):
    if filename not in DOCUMENTS:raise HTTPException(404,'Unknown Hearthside document')
    root,relative,mode=_source()
    # Full's identity is the actual Bot SOUL, never a second project copy.
    parts=(filename,) if mode=='project' and filename=='SOUL.md' else relative+(filename,)
    data,info=_open(root,parts)
    if b'\0'in data:raise HTTPException(415,'Project document must be UTF-8 text')
    try:text=data.decode('utf-8')
    except UnicodeError:raise HTTPException(415,'Project document must be UTF-8 text')from None
    response.headers['Cache-Control']='no-store'
    return {'text':text,'mtimeMs':info.st_mtime_ns/1_000_000,'size':len(data),'source':mode}

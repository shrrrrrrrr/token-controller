"""Wrap one court worker, not the idle web service, in one ordinary workflow."""
from contextvars import ContextVar
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat

from .task_client import TaskClient, TaskError

_workflow=ContextVar('simcourt_workflow',default=None)


@contextmanager
def court_task(private_config,model):
    client=TaskClient.from_file(private_config)
    try:
        with client.workflow(model,kind='simcourt',label='SimCourt 庭审') as task:
            token=_workflow.set(task)
            try:yield task.id
            finally:_workflow.reset(token)
    finally:client.close()


def model_headers():
    task=_workflow.get()
    if task is None:raise TaskError('court_workflow_not_registered')
    return task.headers


def chat(messages,**options):
    task=_workflow.get()
    if task is None:raise TaskError('court_workflow_not_registered')
    return task.chat(messages,**options)


@contextmanager
def linked_court_task(mapping_file,authenticated_principal,model):
    """Principal must come from SimCourt server authentication, never a form field.

    Operator maps each existing SimCourt identity to its own scoped member Key.
    No arbitrary user_id or cross-user impersonation header is accepted by gateway.
    """
    path=Path(mapping_file);entry=path.lstat()
    if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink!=1 or entry.st_size>65536
            or os.name!='nt' and (entry.st_uid!=os.getuid() or stat.S_IMODE(entry.st_mode)!=0o600)):
        raise TaskError('unsafe_simcourt_identity_mapping')
    document=json.loads(path.read_text())
    if not isinstance(document,dict) or set(document)!={'principals'} or not isinstance(document['principals'],dict):
        raise TaskError('invalid_simcourt_identity_mapping')
    config=document['principals'].get(authenticated_principal)
    if not isinstance(config,str) or not Path(config).is_absolute():raise TaskError('simcourt_identity_not_linked')
    with court_task(config,model) as task_id:yield task_id

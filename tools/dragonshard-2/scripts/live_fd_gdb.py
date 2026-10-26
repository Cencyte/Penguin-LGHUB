"""GDB-side syscall-only descriptor receiver; used by live_fd_storage.py."""
import json
import os
import signal
import struct
import traceback
import gdb

plan = json.load(open(os.environ['DRAGONSHARD_FD_PLAN']))
pid = plan['pid']
attached = False
memory = None
sock = None
result = {'pid': pid, 'complete': False, 'replaced': []}


def syscall(number, *arguments):
    expression = '(long)syscall(' + ','.join(str(n) for n in (number, *arguments)) + ')'
    value = int(gdb.parse_and_eval(expression))
    if value < 0:
        raise RuntimeError(f'syscall {number} returned {value}')
    return value


try:
    gdb.execute('set pagination off')
    gdb.execute('set confirm off')
    gdb.execute('set debuginfod enabled off')
    gdb.execute('set unwind-on-signal on')
    gdb.execute('handle SIGSTOP nostop noprint nopass')
    gdb.execute(f'attach {pid}')
    attached = True
    gdb.execute('set scheduler-locking on')
    inferior = gdb.selected_inferior()
    memory = syscall(9, 0, 8192, 3, 0x22, -1, 0)  # anonymous read/write workspace
    address = struct.pack('H', 1) + os.fsencode(plan['socket']) + b'\0'
    if len(address) > 110:
        raise RuntimeError('Unix socket path too long')
    inferior.write_memory(memory, address)
    sock = syscall(41, 1, 5 | os.O_CLOEXEC, 0)  # AF_UNIX, SOCK_SEQPACKET
    syscall(42, sock, memory, len(address))
    for entry in plan['fds']:
        # One data byte plus one SCM_RIGHTS descriptor, preserving the sender's
        # open-file description (including positions shared between processes).
        inferior.write_memory(memory + 256, struct.pack('QQ', memory + 512, 1))
        inferior.write_memory(memory + 320, b'\0' * 32)
        header = struct.pack('QI4xQQQQi4x', 0, 0, memory + 256, 1, memory + 320, 32, 0)
        inferior.write_memory(memory + 128, header)
        if syscall(47, sock, memory + 128, 0x40000000) != 1:
            raise RuntimeError('Missing descriptor message')
        control = bytes(inferior.read_memory(memory + 320, 20))
        length, level, kind, received = struct.unpack('Qiii', control)
        if (length, level, kind) != (20, 1, 1):
            raise RuntimeError('Unexpected ancillary descriptor message')
        syscall(292, received, entry['fd'], entry['flags'] & os.O_CLOEXEC)  # dup3
        syscall(3, received)
        result['replaced'].append(entry['fd'])
    # Closing any descriptor for an inode drops this process's POSIX locks on
    # it. Restore the recorded locks AFTER all temporary descriptors close.
    for entry in plan['fds']:
        for lock in entry['locks']:
            data = struct.pack('hh4xqqi4x', 1 if lock['type'] == 'WRITE' else 0,
                               0, lock['start'], lock['length'], 0)
            inferior.write_memory(memory + 768, data)
            syscall(72, entry['fd'], 6, memory + 768)  # fcntl(F_SETLK)
    syscall(3, sock); sock = None
    syscall(11, memory, 8192); memory = None
    result['complete'] = True
except BaseException:
    result['error'] = traceback.format_exc()
finally:
    if attached:
        # Never let an inferior run between descriptor replacement and the
        # controller's cross-process verification / path publication.
        os.kill(pid, signal.SIGSTOP)
        gdb.execute('detach')
    with open(plan['result'], 'w') as stream:
        json.dump(result, stream, indent=2)

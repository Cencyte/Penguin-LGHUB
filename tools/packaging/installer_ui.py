"""Compact terminal presentation and complete command logging for setup."""
import hashlib
import errno
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
import threading
import time

from windows_media_acquire import ROOT

STYLE=json.loads((ROOT/'packaging/installer.json').read_text())
GHUB_VERSION=json.loads((ROOT/'packaging/logitech-depots.lock.json').read_text())['application_version']


def logo_format(env):
    # Do not send graphics escape sequences to unknown terminals/multiplexers.
    if env.get('TMUX') or env.get('STY'):return 'symbols'
    term=env.get('TERM','')
    # Konsole's sixel decoder initializes unpainted pixels as opaque black.
    # Its Kitty RGBA path preserves alpha; no theme-colored matte is needed.
    try:konsole=int(env.get('KONSOLE_VERSION','0'))
    except ValueError:konsole=0
    if konsole>=220400:return 'kitty'
    if 'kitty' in term or env.get('KITTY_WINDOW_ID') or env.get('TERM_PROGRAM') in ('WezTerm','ghostty'):return 'kitty'
    if term.startswith(('foot','mlterm')) or 'sixel' in term:return 'sixels'
    return 'symbols'


class Presentation:
    def __init__(self, log_path, verbose=False, stream=None):
        self.stream=stream or sys.stdout;self.verbose=verbose
        self.log_path=log_path
        log_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.log=log_path.open('x',encoding='utf-8');log_path.chmod(0o600)
        self.color=self.stream.isatty() and 'NO_COLOR' not in os.environ
        self.label_width=max(map(len,STYLE['category_colors']))+2
        columns=100
        if self.stream.isatty():
            try:columns=os.get_terminal_size(self.stream.fileno()).columns
            except (OSError,ValueError,AttributeError):pass
        self.description_width=max(20,min(70,columns-self.label_width-10))
        self.pending_check=None
        self.check_results=False
        self.terminal_available=True
        self._terminal_lock=threading.RLock()
        self._spinner_visible=False
        self._spinner_ready=False
        self._spinner_suspended=0
        self._line_start=True
        self._activity_thread=None
        self._activity_stop=threading.Event()

    def write(self,text):
        self.terminal_write(text+'\n')

    def notice(self,text,error=False):
        color='\033[31m' if error and self.color else ''
        reset='\033[0m' if color else ''
        self.write(color+text+reset)
        self.log.write(text+'\n');self.log.flush()

    def terminal_write(self,text):
        with self._terminal_lock:
            self._clear_spinner()
            self._emit(text)
            if text:self._line_start=text.endswith('\n')

    def _emit(self,text):
        if not self.terminal_available:return
        try:
            self.stream.write(text);self.stream.flush()
        except OSError as exc:
            if exc.errno not in (errno.EPIPE,errno.EIO,errno.EBADF):raise
            # Terminal closure must not prevent transactional cleanup.
            self.terminal_available=False

    def _clear_spinner(self):
        if self._spinner_visible:
            self._emit('\r\033[2K')
            self._spinner_visible=False

    def _animate(self):
        frames='|/-\\';index=0
        while not self._activity_stop.wait(.125):
            with self._terminal_lock:
                if (self.terminal_available and self._spinner_ready and
                        not self._spinner_suspended and self._line_start):
                    self._emit('\r\033[2K'+frames[index % len(frames)])
                    self._spinner_visible=True;index+=1

    @contextmanager
    def activity(self):
        """Animate the empty row below output; redirected/dumb output stays plain."""
        if self._activity_thread is not None or not self.stream.isatty() or os.environ.get('TERM','dumb')=='dumb':
            yield
            return
        self._activity_stop.clear()
        self._activity_thread=threading.Thread(target=self._animate,name='installer-spinner',daemon=True)
        self._activity_thread.start()
        try:yield
        finally:self._stop_activity()

    @contextmanager
    def suspend_activity(self):
        # sudo writes its password prompt directly to the controlling terminal.
        # Never repaint that row while it may be waiting for authorization.
        with self._terminal_lock:
            self._spinner_suspended+=1;self._clear_spinner()
        try:yield
        finally:
            with self._terminal_lock:self._spinner_suspended-=1

    def _stop_activity(self):
        self._activity_stop.set()
        if self._activity_thread is not None:
            self._activity_thread.join()
            self._activity_thread=None
        with self._terminal_lock:self._clear_spinner()

    def output(self,text):
        """Keep complete diagnostic output, showing it only in verbose mode."""
        self.log.write(text);self.log.flush()
        if self.verbose:
            self.terminal_write(text)

    def header(self, report_path=None):
        if self.stream.isatty():
            folder=ROOT/'assets/terminal';meta=json.loads((folder/'render.json').read_text())
            fmt=logo_format(os.environ);p=folder/(fmt+'.ansi');data=p.read_bytes()
            if hashlib.sha256(data).hexdigest()!=meta['files'][p.name]:raise ValueError('Terminal artwork changed')
            self.stream.write(data.decode('utf-8'));self.stream.write('\033[0m\033[?25h\n')
        white='\033[0;37m' if self.color else '';reset='\033[0m' if self.color else ''
        for text in (f"Penguin-LGHUB v{STYLE['project_version']}",
                     f"Installer version v{STYLE['installer_version']}.",
                     f'Logitech G HUB v{GHUB_VERSION}', '',
                     f'Report file: {report_path}' if report_path else f'Log file: {self.log_path}', ''):
            self.write(white+text+reset);self.log.write(text+'\n')
        self.log.flush()

    def step(self,category,description,status=None):
        label='['+category+']'
        color=f"\033[38;5;{STYLE['category_colors'][category]}m" if self.color else ''
        reset='\033[0m' if self.color else ''
        lines=textwrap.wrap(description,width=self.description_width-3,break_long_words=False) or ['']
        for index,line in enumerate(lines):
            prefix=(color+label+reset+' '*(self.label_width-len(label)) if index==0 else ' '*self.label_width)+' '
            body=line
            if index==len(lines)-1:
                body+='...'
                if status:body=body.ljust(self.description_width)+' '+status
            self.write(prefix+body)
            plain=(label.ljust(self.label_width) if index==0 else ' '*self.label_width)+' '+body
            self.log.write(time.strftime('%Y-%m-%dT%H:%M:%S%z')+' '+plain+'\n')
        self.log.flush()
        self._spinner_ready=True

    def begin_check(self,category,description):
        self.pending_check=(category,description)
        if not self.check_results:self.step(category,description)

    def finish_check(self,category,description,status):
        if self.check_results:self.step(category,description,status)
        self.pending_check=None

    def fail_check(self):
        if self.pending_check:
            if self.check_results:self.step(*self.pending_check,status='FAIL')
            self.pending_check=None

    def complete(self,action):
        message=f'🎉 {action} complete! 🎉'
        self.write('');self.write(message)
        self.log.write('\n'+message+'\n');self.log.flush()

    def run(self,argv,env=None):
        argv=list(map(str,argv))
        with self.suspend_activity() if Path(argv[0]).name=='sudo' else nullcontext():
            if Path(argv[0]).name=='sudo':self.wait_for_sudo(env)
            self._run(argv,env)

    def wait_for_sudo(self,env=None):
        """Wait for the person, then let sudo own its normal password prompt.

        A long build must not start sudo's password countdown while unattended.
        No password is read here, no policy is changed, and there is no timer
        on the Enter prompt. Cached/passwordless authorization needs no pause.
        """
        check=subprocess.run(['sudo','-n','-v'],env=env,stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                             text=True,errors='replace',timeout=15)
        self.output('[sudo authorization check: exit '+str(check.returncode)+']\n'+check.stdout)
        if check.returncode==0:return
        try:
            terminal=open('/dev/tty','r')
        except OSError as error:
            raise RuntimeError('Administrator authorization needs a terminal. Run the installer '
                               'from your desktop terminal so sudo can request your password.') from error
        with terminal:
            self.write('')
            self.write('Setup is paused for administrator authorization; there is no countdown.')
            self.write('Press Enter when ready for the sudo password prompt, or Ctrl+C to cancel.')
            self.log.write('Waiting for Enter before starting sudo authentication.\n');self.log.flush()
            if not terminal.readline():
                raise RuntimeError('The authorization terminal closed before sudo could start.')

    def _run(self,argv,env=None):
        argv=list(map(str,argv));self.log.write('$ '+shlex.join(argv)+'\n');self.log.flush()
        # stdin remains the real terminal for scoped sudo authorization. Never
        # log passwords or feed authorization through a captured input stream.
        # sudo must retain the controlling terminal for its own password prompt.
        separate_session=Path(argv[0]).name!='sudo'
        proc=subprocess.Popen(argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,errors='replace',bufsize=1,start_new_session=separate_session)
        try:
            for line in proc.stdout:
                self.output(line)
            code=proc.wait()
        except BaseException:
            import signal
            if separate_session:os.killpg(proc.pid,signal.SIGTERM)
            else:proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if separate_session:os.killpg(proc.pid,signal.SIGKILL)
                else:proc.kill()
                proc.wait()
            raise
        finally:proc.stdout.close()
        self.log.write(f'[exit {code}]\n');self.log.flush()
        if code:raise subprocess.CalledProcessError(code,argv)

    def close(self):
        self._stop_activity()
        self.log.close()

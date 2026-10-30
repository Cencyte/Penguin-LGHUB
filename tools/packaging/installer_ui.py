"""Compact terminal presentation and complete command logging for setup."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from windows_media_acquire import ROOT

STYLE=json.loads((ROOT/'packaging/installer.json').read_text())


def logo_format(env):
    # Do not send graphics escape sequences to unknown terminals/multiplexers.
    if env.get('TMUX') or env.get('STY'):return 'symbols'
    term=env.get('TERM','')
    if 'kitty' in term or env.get('KITTY_WINDOW_ID') or env.get('TERM_PROGRAM') in ('WezTerm','ghostty'):return 'kitty'
    if env.get('KONSOLE_VERSION') or term.startswith(('foot','mlterm')) or 'sixel' in term:return 'sixels'
    return 'symbols'


class Presentation:
    def __init__(self, log_path, verbose=False, stream=None):
        self.stream=stream or sys.stdout;self.verbose=verbose
        self.log_path=log_path
        log_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.log=log_path.open('x',encoding='utf-8');log_path.chmod(0o600)
        self.color=self.stream.isatty() and 'NO_COLOR' not in os.environ

    def write(self,text):
        self.stream.write(text+'\n');self.stream.flush()

    def output(self,text):
        """Keep complete diagnostic output, showing it only in verbose mode."""
        self.log.write(text);self.log.flush()
        if self.verbose:
            self.stream.write(text);self.stream.flush()

    def header(self):
        if self.stream.isatty():
            folder=ROOT/'assets/terminal';meta=json.loads((folder/'render.json').read_text())
            fmt=logo_format(os.environ);p=folder/(fmt+'.ansi');data=p.read_bytes()
            if hashlib.sha256(data).hexdigest()!=meta['files'][p.name]:raise ValueError('Terminal artwork changed')
            self.stream.write(data.decode('utf-8'));self.stream.write('\033[0m\033[?25h\n')
        white='\033[0;37m' if self.color else '';reset='\033[0m' if self.color else ''
        for text in (f"Penguin-LGHUB v{STYLE['project_version']}",
                     f"Installer version v{STYLE['installer_version']}.",f'Log file: {self.log_path}'):
            self.write(white+text+reset);self.log.write(text+'\n')
        self.log.flush()

    def step(self,category,description):
        label='['+category+']'
        text=label+' '+description+'...'
        color=f"\033[38;5;{STYLE['category_colors'][category]}m" if self.color else ''
        reset='\033[0m' if self.color else ''
        self.write(color+label+reset+' '+description+'...')
        self.log.write(time.strftime('%Y-%m-%dT%H:%M:%S%z')+' '+text+'\n');self.log.flush()

    def run(self,argv,env=None):
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

    def close(self):self.log.close()

#!/usr/bin/env python
# -*- coding: utf-8 -*-

from threading import Thread
import logging
import shlex
from subprocess import Popen, PIPE
from select import select
from crossplatform import CrossPlatform


class StreamIsNotAvailable(Exception):
    pass


class Process(object):
    def __init__(self, server, name):
        self._server = server
        if name is None:
            name = 'Unknown'
        self._name = name

    def run(self, args):
        cmdline = self.prepare(args)
        logging.info(self._name + ' start: ' + ' '.join(cmdline))
        p = Popen(cmdline, stdout=PIPE, stderr=PIPE)
        self.p = p
        return self

    def output_fds(self):
        return (self.p.stdout, self.p.stderr)

    def kill_and_wait(self):
        try:
            self.p.kill()
        except OSError:
            # maybe already dead
            pass
        try:
            self.p.wait()
        except OSError:
            # maybe already recycled
            pass

    @property
    def name(self):
        return self._name

    @property
    def returncode(self):
        self.p.poll()
        return self.p.returncode

    def is_dead(self):
        self.p.poll()
        return self.p.returncode is not None


class FfmpegProcess(Process):
    def __init__(self, server):
        super(FfmpegProcess, self).__init__(server, 'ffmpeg')

    def prepare(self, args):
        params = (args['video_input'] +
                  ' {audio_input}'
                  ' {video_output}'
                  ' {audio_output}'
                  ' -map 0 -map 1 -f flv tcp://127.0.0.1:9999'
                  ).format(video_input=args['video_input'],
                           video_output=args['video_output'],
                           audio_input=args['audio_input'],
                           audio_output=args['audio_output'],
                           x=args['x'],
                           y=args['y'],
                           w=args['w'],
                           h=args['h'])
        return ['ffmpeg'] + shlex.split(params)

    def process(self, fd):
        if fd not in (self.p.stdout, self.p.stderr):
            return
        logging.debug(self._name + ': ' + fd.readline().strip())


class ServerProcess(Process):
    def __init__(self, server):
        super(ServerProcess, self).__init__(server, 'server')

    def prepare(self, args):
        params = (CrossPlatform.get().share_path('crtmpserver.lua'))
        return ['crtmpserver'] + shlex.split(params)

    def process(self, fd):
        if fd not in (self.p.stdout, self.p.stderr):
            return
        line = fd.readline().strip()
        logging.debug('SERVER: ' + line)
        if hasattr(self, '_url'):
            return
        index = line.find('Stream INLFLV(1) with name')
        if index < 0:
            return
        fields = line[index:].split('`', 2)
        if len(fields) != 3:
            logging.debug('server_process fields counts != 3: {}'.
                          format(fields))
            return
        self._server._url = "rtmp://{ip}:1936/flvplayback/" + fields[1]
        self._server.status = self._server.S_STARTED


class StreamServer(Thread):
    S_STOPPED = 0
    S_STARTING = 1
    S_STARTED = 2
    S_STOPPING = 3

    def __init__(self, args, callback):
        Thread.__init__(self)
        self._args = args
        self._callback = callback
        self._status = self.S_STOPPED

    def run(self):
        class ProcessDead(Exception):
            pass

        logging.info('StreamServer Start')

        self.status = self.S_STARTING
        self.p_server = ServerProcess(self).run(self._args)
        self.p_ffmpeg = FfmpegProcess(self).run(self._args)
        procs = (self.p_ffmpeg, self.p_server)
        inputs = []
        map(lambda p: inputs.extend(p.output_fds()), procs)

        try:
            while True:
                R, W, E = select(inputs, [], [])
                for p in procs:
                    if p.is_dead():
                        raise ProcessDead()
                for fd in R:
                    map(lambda p: p.process(fd), procs)
        #except ProcessDead:
        except:
            pass
        self.stop()

    def stop(self):
        self.status = self.S_STOPPING
        map(lambda p: p.kill_and_wait(), (self.p_ffmpeg, self.p_server))
        logging.info('StreamServer stop')
        self.status = self.S_STOPPED

    @property
    def args(self):
        return self._args

    @property
    def url(self):
        if not hasattr(self, '_url'):
            raise StreamIsNotAvailable
        return self._url

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, status):
        if self._status == status:
            return
        self._status = status
        try:
            self._callback(self._status)
        except:
            pass

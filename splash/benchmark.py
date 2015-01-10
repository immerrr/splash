#!/usr/bin/env python

"""
This is a script for benchmarking PNG rendering.

It performs all the initialization like splash server but renderer script
returns benchmark results instead of a webpage snapshot.  As of now, it only
fetches one hard-coded webpage, too.

"""


from resource import getrusage, RUSAGE_SELF
import sys
import time
import pprint

from twisted.internet import task, defer

from splash.qtrender import PngRender
from splash import network_manager, xvfb
from splash.server import parse_opts, _default_proxy_factory, start_logging
from splash.render_options import RenderOptions
from splash.qtutils import init_qt_app


def install_qtreactor(verbose):
    init_qt_app(verbose)

    from qtreactor .qt4base import QtReactor

    class StoppingReactor(QtReactor):
        """
        For some reason, simply stopping reactor won't stop qt event loop.

        In usual scenario, there's QtReactor.do_iteration callback that is
        scheduled by timer and the last timer event will do qApp.quit(), but
        for some reason after executing the benchmark that last event never
        arrives.

        """
        def stop(self):
            super(StoppingReactor, self).stop()
            self.qApp.exit(0)

    # r = QtReactor()
    r = StoppingReactor()
    from twisted.internet.main import installReactor
    installReactor(r)


class PngRenderBenchmarkScript(PngRender):
    """
    Run PNG rendering several times and instead of the picture return the
    result of the benchmark.

    """
    NUM_REPEATS = 3

    def get_result(self):
        rusage_before = getrusage(RUSAGE_SELF)
        stime = time.time()
        for i in xrange(self.NUM_REPEATS):
            self.tab.png(self.width, self.height)
        etime = time.time()
        rusage_after = getrusage(RUSAGE_SELF)
        total_wallclock_time = etime - stime
        total_cpu_time = (rusage_after.ru_utime + rusage_after.ru_stime -
                          rusage_before.ru_utime - rusage_before.ru_stime)

        # on Mac OS X ru_maxrss is in bytes, on Linux it is in KB
        if sys.platform != 'darwin':
            rss_multi = 1024
        else:
            rss_multi = 1
        return {
            'wallclock_secs': total_wallclock_time / float(self.NUM_REPEATS),
            'cpu_secs': total_cpu_time / float(self.NUM_REPEATS),
            'maxrss_before': rusage_before.ru_maxrss * rss_multi,
            'maxrss_after': rusage_after.ru_maxrss * rss_multi,
        }


def main_async(reactor, opts):
    render_options = {
        'url': 'http://edition.cnn.com/',
        'viewport': 'full', 'wait': 0.5, 'width': 1000}
    print "Render benchmark options:"
    pprint.pprint(render_options)

    net_mgr = network_manager.SplashQNetworkAccessManager(
        filters_path=opts.filters_path,
        allowed_schemes=opts.allowed_schemes.split(',') + ['file'],
        verbosity=opts.verbosity)
    render_options['uid'] = 100500
    render_options = RenderOptions(render_options)
    renderer = PngRenderBenchmarkScript(
        network_manager=net_mgr,
        splash_proxy_factory=_default_proxy_factory(opts.proxy_profiles_path),
        render_options=render_options,
        verbosity=opts.verbosity)

    renderer_params = render_options.get_common_params(opts.js_profiles_path)
    renderer_params.update(render_options.get_png_params())
    renderer_params.pop('proxy')
    renderer.start(**renderer_params)

    def cb(val):
        print "Png rendering benchmark result:"
        pprint.pprint(val)

    def eb(err):
        print err.getTraceback()
        return err

    renderer.deferred.addCallback(cb)
    renderer.deferred.addErrback(eb)
    renderer.deferred.addBoth(lambda _: renderer.close())
    return renderer.deferred


def main():
    opts, _ = parse_opts()
    start_logging(opts)
    install_qtreactor(opts.verbosity >= 5)
    with xvfb.autostart(opts.disable_xvfb) as x:
        xvfb.log_options(x)
        task.react(main_async, (opts,))


if __name__ == '__main__':
    main()

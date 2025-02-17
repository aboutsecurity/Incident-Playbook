#!/usr/bin/env python
"""This is a backend analysis worker which will be deployed on the server."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from absl import app
from absl import flags

# pylint: disable=unused-import,g-bad-import-order
from grr_response_server import server_plugins
# pylint: enable=unused-import,g-bad-import-order

from grr_response_core import config
from grr_response_core.config import contexts
from grr_response_core.config import server as config_server
from grr_response_server import fleetspeak_connector
from grr_response_server import server_startup
from grr_response_server import worker_lib


flags.DEFINE_bool(
    "version",
    default=False,
    allow_override=True,
    help="Print the GRR worker version number and exit immediately.")


def main(argv):
  """Main."""
  del argv  # Unused.

  if flags.FLAGS.version:
    print("GRR worker {}".format(config_server.VERSION["packageversion"]))
    return

  config.CONFIG.AddContext(contexts.WORKER_CONTEXT,
                           "Context applied when running a worker.")

  # Initialise flows and config_lib
  server_startup.Init()

  fleetspeak_connector.Init()

  worker_obj = worker_lib.GRRWorker()
  worker_obj.Run()


if __name__ == "__main__":
  app.run(main)

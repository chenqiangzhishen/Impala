# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import pytest

from tests.common.custom_cluster_test_suite import CustomClusterTestSuite
from tests.common.skip import SkipIf

@SkipIf.no_file_handle_caching
class TestHdfsFdCaching(CustomClusterTestSuite):
  """Tests that if HDFS file handle caching is enabled, file handles are actually cached
  and the associated metrics return valid results. In addition, tests that the upper bound
  of cached file handles is respected."""

  NUM_ROWS = 100
  INSERT_TPL = "insert into cachefd.simple values"

  @classmethod
  def get_workload(self):
    return 'functional-query'

  def create_n_files(self, n):
    """Creates 'n' files by performing 'n' inserts with NUM_ROWS rows."""
    values = ", ".join(["({0},{0},{0})".format(x) for x in range(self.NUM_ROWS)])
    for _ in range(n):
      self.client.execute(self.INSERT_TPL + values)

  def setup_method(self, method):
    super(TestHdfsFdCaching, self).setup_method(method)
    impalad = self.cluster.impalads[0]
    client = impalad.service.create_beeswax_client()

    self.client = client
    client.execute("drop database if exists cachefd cascade")
    client.execute("create database cachefd")
    client.execute("create table cachefd.simple(id int, col1 int, col2 int) "
                   "stored as parquet")
    self.create_n_files(1)

  def teardown_method(self, method):
    super(TestHdfsFdCaching, self).teardown_method(method)
    self.client.execute("drop database if exists cachefd cascade")

  @pytest.mark.execute_serially
  @CustomClusterTestSuite.with_args(
      impalad_args="--max_cached_file_handles=16",
      catalogd_args="--load_catalog_in_background=false")
  def test_scan_does_cache_fd(self, vector):
    """Tests that an hdfs scan will lead to caching HDFS file descriptors."""

    # Maximum number of file handles cached
    assert self.max_cached_handles() <= 16
    # The table has one file, so there should be one more handle cached after the
    # first select.
    num_handles_before = self.cached_handles()
    self.execute_query("select * from cachefd.simple", vector=vector)
    num_handles_after = self.cached_handles()
    assert self.max_cached_handles() <= 16

    # Should have one more file handle
    assert num_handles_after == (num_handles_before + 1)

    # No open handles if scanning is finished
    assert self.outstanding_handles() == 0

    # No change when reading the table again
    for x in range(10):
      self.execute_query("select * from cachefd.simple", vector=vector)
      assert self.cached_handles() == num_handles_after
      assert self.max_cached_handles() <= 16
      assert self.outstanding_handles() == 0

    # Create more files. This means there are more files than the cache size.
    # The cache size should still be enforced.
    self.create_n_files(100)

    # Read all the files of the table and make sure no FD leak
    for x in range(10):
      self.execute_query("select count(*) from cachefd.simple;", vector=vector)
      assert self.max_cached_handles() <= 16
    assert self.outstanding_handles() == 0

  def cached_handles(self):
    return self.get_agg_metric("impala-server.io.mgr.num-cached-file-handles")

  def outstanding_handles(self):
    return self.get_agg_metric("impala-server.io.mgr.num-file-handles-outstanding")

  def max_cached_handles(self):
    return self.get_agg_metric("impala-server.io.mgr.num-cached-file-handles", max)

  def get_agg_metric(self, key, fun=sum):
    cluster = self.cluster
    return fun([s.service.get_metric_value(key) for s in cluster.impalads])

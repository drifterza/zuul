# Copyright 2014 Rackspace Australia
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import sqlalchemy as sa

from tests.base import ZuulTestCase, ZuulDBTestCase


def _get_reporter_from_connection_name(reporters, connection_name):
    # Reporters are placed into lists for each action they may exist in.
    # Search through the given list for the correct reporter by its conncetion
    # name
    for r in reporters:
        if r.connection.connection_name == connection_name:
            return r


class TestConnections(ZuulTestCase):
    config_file = 'zuul-connections-same-gerrit.conf'
    tenant_config_file = 'config/zuul-connections-same-gerrit/main.yaml'

    def test_multiple_gerrit_connections(self):
        "Test multiple connections to the one gerrit"

        A = self.fake_review_gerrit.addFakeChange('org/project', 'master', 'A')
        self.addEvent('review_gerrit', A.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertEqual(len(A.patchsets[-1]['approvals']), 1)
        self.assertEqual(A.patchsets[-1]['approvals'][0]['type'], 'verified')
        self.assertEqual(A.patchsets[-1]['approvals'][0]['value'], '1')
        self.assertEqual(A.patchsets[-1]['approvals'][0]['by']['username'],
                         'jenkins')

        B = self.fake_review_gerrit.addFakeChange('org/project', 'master', 'B')
        self.executor_server.failJob('project-test2', B)
        self.addEvent('review_gerrit', B.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertEqual(len(B.patchsets[-1]['approvals']), 1)
        self.assertEqual(B.patchsets[-1]['approvals'][0]['type'], 'verified')
        self.assertEqual(B.patchsets[-1]['approvals'][0]['value'], '-1')
        self.assertEqual(B.patchsets[-1]['approvals'][0]['by']['username'],
                         'civoter')


class TestSQLConnection(ZuulDBTestCase):
    config_file = 'zuul-sql-driver.conf'
    tenant_config_file = 'config/sql-driver/main.yaml'

    def test_sql_tables_created(self, metadata_table=None):
        "Test the tables for storing results are created properly"
        buildset_table = 'zuul_buildset'
        build_table = 'zuul_build'

        insp = sa.engine.reflection.Inspector(
            self.connections.connections['resultsdb'].engine)

        self.assertEqual(9, len(insp.get_columns(buildset_table)))
        self.assertEqual(10, len(insp.get_columns(build_table)))

    def test_sql_results(self):
        "Test results are entered into an sql table"
        # Grab the sa tables
        tenant = self.sched.abide.tenants.get('tenant-one')
        reporter = _get_reporter_from_connection_name(
            tenant.layout.pipelines['check'].success_actions,
            'resultsdb'
        )

        # Add a success result
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # Add a failed result for a negative score
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')

        self.executor_server.failJob('project-test1', B)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        conn = self.connections.connections['resultsdb'].engine.connect()
        result = conn.execute(
            sa.sql.select([reporter.connection.zuul_buildset_table]))

        buildsets = result.fetchall()
        self.assertEqual(2, len(buildsets))
        buildset0 = buildsets[0]
        buildset1 = buildsets[1]

        self.assertEqual('check', buildset0['pipeline'])
        self.assertEqual('org/project', buildset0['project'])
        self.assertEqual(1, buildset0['change'])
        self.assertEqual(1, buildset0['patchset'])
        self.assertEqual(1, buildset0['score'])
        self.assertEqual('Build succeeded.', buildset0['message'])

        buildset0_builds = conn.execute(
            sa.sql.select([reporter.connection.zuul_build_table]).
            where(
                reporter.connection.zuul_build_table.c.buildset_id ==
                buildset0['id']
            )
        ).fetchall()

        # Check the first result, which should be the project-merge job
        self.assertEqual('project-merge', buildset0_builds[0]['job_name'])
        self.assertEqual("SUCCESS", buildset0_builds[0]['result'])
        self.assertEqual(
            'finger://zl.example.com/{uuid}'.format(
                uuid=buildset0_builds[0]['uuid']),
            buildset0_builds[0]['log_url'])
        self.assertEqual('check', buildset1['pipeline'])
        self.assertEqual('org/project', buildset1['project'])
        self.assertEqual(2, buildset1['change'])
        self.assertEqual(1, buildset1['patchset'])
        self.assertEqual(-1, buildset1['score'])
        self.assertEqual('Build failed.', buildset1['message'])

        buildset1_builds = conn.execute(
            sa.sql.select([reporter.connection.zuul_build_table]).
            where(
                reporter.connection.zuul_build_table.c.buildset_id ==
                buildset1['id']
            )
        ).fetchall()

        # Check the second last result, which should be the project-test1 job
        # which failed
        self.assertEqual('project-test1', buildset1_builds[-2]['job_name'])
        self.assertEqual("FAILURE", buildset1_builds[-2]['result'])
        self.assertEqual(
            'finger://zl.example.com/{uuid}'.format(
                uuid=buildset1_builds[-2]['uuid']),
            buildset1_builds[-2]['log_url'])

    def test_multiple_sql_connections(self):
        "Test putting results in different databases"
        # Add a successful result
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # Add a failed result
        B = self.fake_gerrit.addFakeChange('org/project', 'master', 'B')
        self.executor_server.failJob('project-test1', B)
        self.fake_gerrit.addEvent(B.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()

        # Grab the sa tables for resultsdb
        tenant = self.sched.abide.tenants.get('tenant-one')
        reporter1 = _get_reporter_from_connection_name(
            tenant.layout.pipelines['check'].success_actions,
            'resultsdb'
        )

        conn = self.connections.connections['resultsdb'].engine.connect()
        buildsets_resultsdb = conn.execute(sa.sql.select(
            [reporter1.connection.zuul_buildset_table])).fetchall()
        # Should have been 2 buildset reported to the resultsdb (both success
        # and failure report)
        self.assertEqual(2, len(buildsets_resultsdb))

        # The first one should have passed
        self.assertEqual('check', buildsets_resultsdb[0]['pipeline'])
        self.assertEqual('org/project', buildsets_resultsdb[0]['project'])
        self.assertEqual(1, buildsets_resultsdb[0]['change'])
        self.assertEqual(1, buildsets_resultsdb[0]['patchset'])
        self.assertEqual(1, buildsets_resultsdb[0]['score'])
        self.assertEqual('Build succeeded.', buildsets_resultsdb[0]['message'])

        # Grab the sa tables for resultsdb_failures
        reporter2 = _get_reporter_from_connection_name(
            tenant.layout.pipelines['check'].failure_actions,
            'resultsdb_failures'
        )

        conn = self.connections.connections['resultsdb_failures'].\
            engine.connect()
        buildsets_resultsdb_failures = conn.execute(sa.sql.select(
            [reporter2.connection.zuul_buildset_table])).fetchall()
        # The failure db should only have 1 buildset failed
        self.assertEqual(1, len(buildsets_resultsdb_failures))

        self.assertEqual('check', buildsets_resultsdb_failures[0]['pipeline'])
        self.assertEqual(
            'org/project', buildsets_resultsdb_failures[0]['project'])
        self.assertEqual(2, buildsets_resultsdb_failures[0]['change'])
        self.assertEqual(1, buildsets_resultsdb_failures[0]['patchset'])
        self.assertEqual(-1, buildsets_resultsdb_failures[0]['score'])
        self.assertEqual(
            'Build failed.', buildsets_resultsdb_failures[0]['message'])


class TestConnectionsBadSQL(ZuulDBTestCase):
    config_file = 'zuul-sql-driver-bad.conf'
    tenant_config_file = 'config/sql-driver/main.yaml'

    def test_unable_to_connect(self):
        "Test the SQL reporter fails gracefully when unable to connect"
        self.config.set('zuul', 'layout_config',
                        'tests/fixtures/layout-sql-reporter.yaml')
        self.sched.reconfigure(self.config)

        # Trigger a reporter. If no errors are raised, the reporter has been
        # disabled correctly
        A = self.fake_gerrit.addFakeChange('org/project', 'master', 'A')
        self.fake_gerrit.addEvent(A.getPatchsetCreatedEvent(1))
        self.waitUntilSettled()


class TestMultipleGerrits(ZuulTestCase):
    config_file = 'zuul-connections-multiple-gerrits.conf'
    tenant_config_file = 'config/zuul-connections-multiple-gerrits/main.yaml'

    def test_multiple_project_separate_gerrits(self):
        self.executor_server.hold_jobs_in_build = True

        A = self.fake_another_gerrit.addFakeChange(
            'org/project1', 'master', 'A')
        self.fake_another_gerrit.addEvent(A.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertBuilds([dict(name='project-test2',
                                changes='1,1',
                                project='org/project1',
                                pipeline='another_check')])

        # NOTE(jamielennox): the tests back the git repo for both connections
        # onto the same git repo on the file system. If we just create another
        # fake change the fake_review_gerrit will try to create another 1,1
        # change and git will fail to create the ref. Arbitrarily set it to get
        # around the problem.
        self.fake_review_gerrit.change_number = 50

        B = self.fake_review_gerrit.addFakeChange(
            'org/project1', 'master', 'B')
        self.fake_review_gerrit.addEvent(B.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertBuilds([
            dict(name='project-test2',
                 changes='1,1',
                 project='org/project1',
                 pipeline='another_check'),
            dict(name='project-test1',
                 changes='51,1',
                 project='org/project1',
                 pipeline='review_check'),
        ])

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()

    def test_multiple_project_separate_gerrits_common_pipeline(self):
        self.executor_server.hold_jobs_in_build = True

        A = self.fake_another_gerrit.addFakeChange(
            'org/project2', 'master', 'A')
        self.fake_another_gerrit.addEvent(A.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertBuilds([dict(name='project-test2',
                                changes='1,1',
                                project='org/project2',
                                pipeline='common_check')])

        # NOTE(jamielennox): the tests back the git repo for both connections
        # onto the same git repo on the file system. If we just create another
        # fake change the fake_review_gerrit will try to create another 1,1
        # change and git will fail to create the ref. Arbitrarily set it to get
        # around the problem.
        self.fake_review_gerrit.change_number = 50

        B = self.fake_review_gerrit.addFakeChange(
            'org/project2', 'master', 'B')
        self.fake_review_gerrit.addEvent(B.getPatchsetCreatedEvent(1))

        self.waitUntilSettled()

        self.assertBuilds([
            dict(name='project-test2',
                 changes='1,1',
                 project='org/project2',
                 pipeline='common_check'),
            dict(name='project-test1',
                 changes='51,1',
                 project='org/project2',
                 pipeline='common_check'),
        ])

        self.executor_server.hold_jobs_in_build = False
        self.executor_server.release()
        self.waitUntilSettled()


class TestConnectionsMerger(ZuulTestCase):
    config_file = 'zuul-connections-merger.conf'
    tenant_config_file = 'config/single-tenant/main.yaml'

    def configure_connections(self):
        super(TestConnectionsMerger, self).configure_connections(True)

    def test_connections_merger(self):
        "Test merger only configures source connections"

        self.assertIn("gerrit", self.connections.connections)
        self.assertIn("github", self.connections.connections)
        self.assertNotIn("smtp", self.connections.connections)
        self.assertNotIn("sql", self.connections.connections)
        self.assertNotIn("timer", self.connections.connections)
        self.assertNotIn("zuul", self.connections.connections)

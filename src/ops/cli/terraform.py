# Copyright 2019 Adobe. All rights reserved.
# This file is licensed to you under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License. You may obtain a copy
# of the License at http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR REPRESENTATIONS
# OF ANY KIND, either express or implied. See the License for the specific language
# governing permissions and limitations under the License.

import os
import hashlib
import logging
from ops.cli.parser import SubParserConfig
from ops.terraform.terraform_cmd_generator import TerraformCommandGenerator
from ops.hierarchical.composition_config_generator import TerraformConfigGenerator
from distutils.version import StrictVersion
from ops import validate_ops_version
import pkg_resources

logger = logging.getLogger(__name__)


class TerraformParserConfig(SubParserConfig):
    def get_name(self):
        return 'terraform'

    def get_help(self):
        return 'Wrap common terraform tasks with full templated configuration support'

    def configure(self, parser):
        parser.add_argument('subcommand',
                            help='apply | console | destroy | import | output | plan | refresh | show | taint | template | untaint | validate',
                            type=str)
        parser.add_argument('--var', help='the output var to show', type=str, default='')
        parser.add_argument('--module',
                            help='for use with "taint", "untaint" and "import". The module to use. e.g.: vpc', type=str)
        parser.add_argument('--resource',
                            help='for use with "taint", "untaint" and "import". The resource to target. e.g.: aws_instance.nat',
                            type=str)
        parser.add_argument('--name',
                            help='for use with "import". The name or ID of the imported resource. e.g.: i-abcd1234',
                            type=str)
        parser.add_argument('--plan', help='for use with "show", show the plan instead of the statefile',
                            action='store_true')
        parser.add_argument('--state-location', help='control how the remote states are used',
                            choices=['local', 'remote', 'any'], default='any', type=str)
        parser.add_argument('--force-copy',
                            help='for use with "plan" to do force state change automatically during init phase',
                            action='store_true')
        parser.add_argument('--template-location',
                            help='for use with "template". The folder where to save the tf files, without showing',
                            type=str)
        parser.add_argument('--skip-refresh', help='for use with "plan". Skip refresh of statefile',
                            action='store_false', dest='do_refresh')
        parser.set_defaults(do_refresh=True)
        parser.add_argument('--raw-output',
                            help='for use with "plan". Show raw plan output without piping through terraform landscape - '
                                 'https://github.com/coinbase/terraform-landscape (if terraform landscape is not enabled in opsconfig.yaml '
                                 'this will have no impact)', action='store_true',
                            dest='raw_plan_output')
        parser.set_defaults(raw_plan_output=False)
        parser.add_argument('--path-name',
                            help='in case multiple terraform paths are defined, this allows to specify which one to use when running terraform',
                            type=str)
        parser.add_argument('--terraform-path', type=str, default=None, help='Path to terraform files')
        parser.add_argument('--skip-plan',
                            help='for use with "apply"; runs terraform apply without running a plan first',
                            action='store_true')
        parser.add_argument('--auto-approve',
                            help='for use with "apply". Proceeds with the apply without waiting for user confirmation.',
                            action='store_true')
        parser.add_argument('terraform_args', type=str, nargs='*', help='Extra terraform args')

        return parser

    def get_epilog(self):
        return '''
    Examples:
        # Create/update a new cluster with Terraform
        ops clusters/qe1.yaml terraform plan
        ops clusters/qe1.yaml terraform apply

        # Run Terraform apply without running a plan first
        ops clusters/qe1.yaml terraform apply --skip-plan

        # Get rid of a cluster and all of its components
        ops clusters/qe1.yaml terraform destroy

        # Retrieve all output from a previously created Terraform cluster
        ops clusters/qe1.yaml terraform output

        # Retrieve a specific output from a previously created Terraform cluster
        ops clusters/qe1.yaml terraform output --var nat_public_ip

        # Refresh a statefile (no longer part of plan)
        ops clusters/qe1.yaml terraform refresh

        # Taint a resource- forces a destroy, then recreate on next plan/apply
        ops clusters/qe1.yaml terraform taint --module vpc --resource aws_instance.nat

        # Untaint a resource
        ops clusters/qe1.yaml terraform untaint --module vpc --resource aws_instance.nat

        # Show the statefile in human-readable form
        ops clusters/qe1.yaml terraform show

        # Show the plan in human-readable form
        ops clusters/qe1.yaml terraform show --plan

        # View parsed jinja on the terminal
        ops clusters/qe1.yaml terraform template

        # Import an unmanaged existing resource to a statefile
        ops clusters/qe1.yaml terraform import --module vpc --resource aws_instance.nat --name i-abcd1234

        # Use the Terraform Console on a cluster
        ops clusters/qe1.yaml terraform console

        # Validate the syntax of Terraform files
        ops clusters/qe1.yaml terraform validate

        # Specify which terraform path to use
        ops clusters/qe1.yaml terraform plan --path-name terraformFolder1
        
        # Run terraform v2 integration
        ops data/env=dev/region=va6/project=ee/cluster=experiments terraform plan
        '''


class TerraformRunner(object):
    def __init__(self, root_dir, cluster_config_path, cluster_config, inventory_generator, ops_config, template,
                 execute):
        self.cluster_config_path = cluster_config_path
        self.cluster_config = cluster_config
        self.root_dir = root_dir
        self.inventory_generator = inventory_generator
        self.ops_config = ops_config
        self.template = template
        self.execute = execute

    def check_ops_version(self):
        # Check if the cluster_config has a strict requirement of OPS version
        # But only if 'ops_min_version' is specified. Not all clusters configs enforce this
        if "terraform" in self.cluster_config.conf:
            if "ops_min_version" in self.cluster_config.conf["terraform"]:
                ops_min_version = str(self.cluster_config.conf["terraform"]["ops_min_version"])
                validate_ops_version(ops_min_version)

    def run(self, args):
        self.check_ops_version()
        terraform_config_path = os.environ.get("TF_CLI_CONFIG_FILE", self.ops_config.terraform_config_path)
        os.environ["TF_CLI_CONFIG_FILE"] = terraform_config_path
        logger.info("Set TF_CLI_CONFIG_FILE=%s", terraform_config_path)
        if os.path.isdir(self.cluster_config_path):
            return self.run_v2_integration(args)
        else:
            return self.run_v1_integration(args)

    def run_v1_integration(self, args):
        return self.run_composition(args, self.cluster_config)

    def run_composition(self, args, config):
        generator = TerraformCommandGenerator(self.root_dir,
                                              config,
                                              self.inventory_generator,
                                              self.ops_config,
                                              self.template)
        return generator.generate(args)

    def run_v2_integration(self, args):
        logging.basicConfig(level=logging.INFO)
        config_path = os.path.join(self.cluster_config_path, '')
        terraform_path = '../ee-k8s-infra/' if args.terraform_path is None else os.path.join(args.terraform_path, '')
        terraform_path = '{}compositions/terraform/'.format(terraform_path)

        ops_config = self.cluster_config.ops_config.config
        composition_order = ops_config["compositions"]["order"]["terraform"]
        excluded_config_keys = ops_config["compositions"]["excluded_config_keys"]

        tf_config_generator = TerraformConfigGenerator(composition_order, excluded_config_keys)
        reverse_order = "destroy" == args.subcommand
        compositions = tf_config_generator.get_sorted_compositions(config_path, reverse=reverse_order)
        if len(compositions) == 0:
            raise Exception("No terraform compositions were detected in {}.".format(config_path))

        return self.run_v2_compositions(args, config_path, tf_config_generator, terraform_path, compositions)

    def run_v2_compositions(self, args, config_path, tf_config_generator, terraform_path, compositions):
        should_finish = False
        return_code = 0
        for composition in compositions:
            if should_finish:
                logger.info("Skipping 'terraform %s' for composition '%s' because of previous failure.", args.subcommand, composition)
                continue

            logger.info("Running composition: %s", composition)
            tf_config_generator.generate_files(config_path, terraform_path, composition)
            command = self.run_v2_composition(args, terraform_path, composition)
            return_code = self.execute(command)
            if return_code != 0:
                should_finish = True
                logger.error("Command finished with nonzero exit code for composition '%s'. Will skip remaining compositions.", composition)

        return return_code

    def run_v2_composition(self, args, terraform_path, composition):
        config = self.cluster_config
        config['terraform'] = {}
        config['terraform']["path"] = "{}{}".format(terraform_path, composition)
        config['terraform']["variables_file"] = "variables.tfvars.json"
        cluster_id = hashlib.md5(self.cluster_config_path.encode('utf-8')).hexdigest()[:6]
        config['cluster'] = "auto_generated_{}".format(cluster_id)
        return self.run_composition(args, config)

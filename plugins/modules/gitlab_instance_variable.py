#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2023, Benedikt Braunger (bebr@adm.ku.dk)
# Based on code:
# Copyright (c) 2020, Florent Madiot (scodeman@scode.io)
# Copyright (c) 2019, Markus Bergholz (markuman@gmail.com)
# GNU General Public License v3.0+ (see LICENSES/GPL-3.0-or-later.txt or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: gitlab_instance_variable
short_description: Creates, updates, or deletes GitLab instance variables
description:
  - Creates a instance variable if it does not exist.
  - When a instance variable does exist, its value will be updated if the values are different.
  - Variables which are untouched in the playbook, but are not untouched in the GitLab instance,
    they stay untouched (I(purge) is C(false)) or will be deleted (I(purge) is C(true)).
author:
  - Benedikt Braunger (@benibr)
requirements:
  - python >= 2.7
  - python-gitlab python module
extends_documentation_fragment:
  - community.general.auth_basic
  - community.general.gitlab
  - community.general.attributes

attributes:
  check_mode:
    support: full
  diff_mode:
    support: none

options:
  state:
    description:
      - Create or delete instance variable.
    default: present
    type: str
    choices: ["present", "absent"]
  purge:
    description:
      - When set to C(true), delete all variables which are not untouched in the task.
    default: false
    type: bool
  vars:
    description:
      - When the list element is a simple key-value pair, set masked and protected to false.
      - When the list element is a dict with the keys I(value), I(masked) and I(protected), the user can
        have full control about whether a value should be masked, protected or both.
      - Support for group variables requires GitLab >= 13.0.
      - A I(value) must be a string or a number.
      - Field I(variable_type) must be a string with either C(env_var), which is the default, or C(file).
      - When a value is masked, it must be in Base64 and have a length of at least 8 characters.
        See GitLab documentation on acceptable values for a masked variable (U(https://docs.gitlab.com/ce/ci/variables/#masked-variables)).
    default: {}
    type: dict
  variables:
    description:
      - A list of dictionaries that represents CI/CD variables.
      - This modules works internal with this sructure, even if the older I(vars) parameter is used.
    default: []
    type: list
    elements: dict
    suboptions:
      name:
        description:
          - The name of the variable.
        type: str
        required: true
      value:
        description:
          - The variable value.
          - Required when I(state=present).
        type: str
      masked:
        description:
          - Wether variable value is masked or not.
        type: bool
        default: false
      protected:
        description:
          - Wether variable value is protected or not.
        type: bool
        default: false
      variable_type:
        description:
          - Wether a variable is an environment variable (C(env_var)) or a file (C(file)).
        type: str
        choices: [ "env_var", "file" ]
        default: env_var
'''


EXAMPLES = r'''
- name: Set or update some CI/CD variables
  community.general.gitlab_instance_variable:
    api_url: https://gitlab.com
    api_token: secret_access_token
    purge: false
    variables:
      - name: ACCESS_KEY_ID
        value: abc1312cba
      - name: SECRET_ACCESS_KEY
        value: 1337
        masked: true
        protected: true
        variable_type: env_var

- name: Delete one variable
  community.general.gitlab_instance_variable:
    api_url: https://gitlab.com
    api_token: secret_access_token
    state: absent
    vars:
      ACCESS_KEY_ID: acab1221cbad
'''

RETURN = r'''
group_variable:
  description: Four lists of the variablenames which were added, updated, removed or exist.
  returned: always
  type: dict
  contains:
    added:
      description: A list of variables which were created.
      returned: always
      type: list
      sample: ['ACCESS_KEY_ID', 'SECRET_ACCESS_KEY']
    untouched:
      description: A list of variables which exist.
      returned: always
      type: list
      sample: ['ACCESS_KEY_ID', 'SECRET_ACCESS_KEY']
    removed:
      description: A list of variables which were deleted.
      returned: always
      type: list
      sample: ['ACCESS_KEY_ID', 'SECRET_ACCESS_KEY']
    updated:
      description: A list of variables whose values were changed.
      returned: always
      type: list
      sample: ['ACCESS_KEY_ID', 'SECRET_ACCESS_KEY']
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.api import basic_auth_argument_spec

GITLAB_IMP_ERR = None
try:
    import gitlab  # noqa: F401, pylint: disable=unused-import
    HAS_GITLAB_PACKAGE = True
except Exception:
    GITLAB_IMP_ERR = traceback.format_exc()
    HAS_GITLAB_PACKAGE = False

from ansible_collections.community.general.plugins.module_utils.gitlab import (
    auth_argument_spec, gitlab_authentication, ensure_gitlab_package, filter_returned_variables
)


class GitlabInstanceVariables(object):

    def __init__(self, module, gitlab_instance):
        self.instance = gitlab_instance
        self._module = module

    def list_all_instance_variables(self):
        page_nb = 1
        variables = []
        vars_page = self.instance.variables.list(page=page_nb)
        while len(vars_page) > 0:
            variables += vars_page
            page_nb += 1
            vars_page = self.instance.variables.list(page=page_nb)
        return variables

    def create_variable(self, var_obj):
        if self._module.check_mode:
            return True
        var = {
            "key": var_obj.get('key'),
            "value": var_obj.get('value'),
            "masked": var_obj.get('masked'),
            "protected": var_obj.get('protected'),
            "variable_type": var_obj.get('variable_type'),
        }
        if var_obj.get('environment_scope') is not None:
            var["environment_scope"] = var_obj.get('environment_scope')

        self.instance.variables.create(var)
        return True

    def update_variable(self, var_obj):
        if self._module.check_mode:
            return True
        self.delete_variable(var_obj)
        self.create_variable(var_obj)
        return True

    def delete_variable(self, var_obj):
        if self._module.check_mode:
            return True
        self.instance.variables.delete(var_obj.get('key'), filter={'environment_scope': var_obj.get('environment_scope')})
        return True


def compare(requested_variables, existing_variables, state):
    # we need to do this, because it was determined in a previous version - more or less buggy
    # basically it is not necessary and might results in more/other bugs!
    # but it is required  and only relevant for check mode!!
    # logic represents state 'present' when not purge. all other can be derived from that
    # untouched => equal in both
    # updated => name and scope are equal
    # added => name and scope does not exist
    untouched = list()
    updated = list()
    added = list()

    if state == 'present':
        existing_key_scope_vars = list()
        for item in existing_variables:
            existing_key_scope_vars.append({'key': item.get('key'), 'environment_scope': item.get('environment_scope')})

        for var in requested_variables:
            if var in existing_variables:
                untouched.append(var)
            else:
                compare_item = {'key': var.get('name'), 'environment_scope': var.get('environment_scope')}
                if compare_item in existing_key_scope_vars:
                    updated.append(var)
                else:
                    added.append(var)

    return untouched, updated, added


def native_python_main(this_gitlab, purge, requested_variables, state, module):

    change = False
    return_value = dict(added=list(), updated=list(), removed=list(), untouched=list())

    gitlab_keys = this_gitlab.list_all_instance_variables()
    before = [x.attributes for x in gitlab_keys]

    gitlab_keys = this_gitlab.list_all_instance_variables()
    existing_variables = filter_returned_variables(gitlab_keys)

    for item in requested_variables:
        item['key'] = item.pop('name')
        item['value'] = str(item.get('value'))
        if item.get('protected') is None:
            item['protected'] = False
        if item.get('masked') is None:
            item['masked'] = False
        if item.get('environment_scope') is None:
            item['environment_scope'] = '*'
        if item.get('variable_type') is None:
            item['variable_type'] = 'env_var'

    if module.check_mode:
        untouched, updated, added = compare(requested_variables, existing_variables, state)

    if state == 'present':
        add_or_update = [x for x in requested_variables if x not in existing_variables]
        for item in add_or_update:
            try:
                if this_gitlab.create_variable(item):
                    return_value['added'].append(item)

            except Exception:
                if this_gitlab.update_variable(item):
                    return_value['updated'].append(item)

        if purge:
            # refetch and filter
            gitlab_keys = this_gitlab.list_all_instance_variables()
            existing_variables = filter_returned_variables(gitlab_keys)

            remove = [x for x in existing_variables if x not in requested_variables]
            for item in remove:
                if this_gitlab.delete_variable(item):
                    return_value['removed'].append(item)

    elif state == 'absent':
        # value does not matter on removing variables.
        # key and environment scope are sufficient
        for item in existing_variables:
            item.pop('value')
            item.pop('variable_type')
        for item in requested_variables:
            item.pop('value')
            item.pop('variable_type')

        if not purge:
            remove_requested = [x for x in requested_variables if x in existing_variables]
            for item in remove_requested:
                if this_gitlab.delete_variable(item):
                    return_value['removed'].append(item)

        else:
            for item in existing_variables:
                if this_gitlab.delete_variable(item):
                    return_value['removed'].append(item)

    if module.check_mode:
        return_value = dict(added=added, updated=updated, removed=return_value['removed'], untouched=untouched)

    if len(return_value['added'] + return_value['removed'] + return_value['updated']) > 0:
        change = True

    gitlab_keys = this_gitlab.list_all_instance_variables()
    after = [x.attributes for x in gitlab_keys]

    return change, return_value, before, after


def main():
    argument_spec = basic_auth_argument_spec()
    argument_spec.update(auth_argument_spec())
    argument_spec.update(
        purge=dict(type='bool', required=False, default=False),
        vars=dict(type='dict', required=False, default=dict(), no_log=True),
        variables=dict(type='list', elements='dict', required=False, default=list(), options=dict(
            name=dict(type='str', required=True),
            value=dict(type='str', no_log=True),
            masked=dict(type='bool', default=False),
            protected=dict(type='bool', default=False),
            environment_scope=dict(type='str', default='*'),
            variable_type=dict(type='str', default='env_var', choices=["env_var", "file"])
        )),
        state=dict(type='str', default="present", choices=["absent", "present"]),
    )

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[
            ['api_username', 'api_token'],
            ['api_username', 'api_oauth_token'],
            ['api_username', 'api_job_token'],
            ['api_token', 'api_oauth_token'],
            ['api_token', 'api_job_token'],
            ['vars', 'variables'],
        ],
        required_together=[
            ['api_username', 'api_password'],
        ],
        required_one_of=[
            ['api_username', 'api_token', 'api_oauth_token', 'api_job_token']
        ],
        supports_check_mode=True
    )
    ensure_gitlab_package(module)

    purge = module.params['purge']
    var_list = module.params['vars']
    state = module.params['state']

    if var_list:
        variables = vars_to_variables(var_list, module)
    else:
        variables = module.params['variables']

    if state == 'present':
        if any(x['value'] is None for x in variables):
            module.fail_json(msg='value parameter is required in state present')

    gitlab_instance = gitlab_authentication(module)

    this_gitlab = GitlabInstanceVariables(module=module, gitlab_instance=gitlab_instance)

    changed, raw_return_value, before, after = native_python_main(this_gitlab, purge, variables, state, module)

    untouched_key_name = 'key'
    if not module.check_mode:
        untouched_key_name = 'name'
        raw_return_value['untouched'] = [x for x in before if x in after]

    added = [x.get('key') for x in raw_return_value['added']]
    updated = [x.get('key') for x in raw_return_value['updated']]
    removed = [x.get('key') for x in raw_return_value['removed']]
    untouched = [x.get(untouched_key_name) for x in raw_return_value['untouched']]
    return_value = dict(added=added, updated=updated, removed=removed, untouched=untouched)

    module.exit_json(changed=changed, group_variable=return_value)


if __name__ == '__main__':
    main()

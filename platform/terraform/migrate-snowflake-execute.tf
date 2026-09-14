# TEMPORARY — delete this file after one successful apply.
#
# snowflake.tf used to build the lakehouse out of `snowflake_execute` resources
# and now uses typed ones. Without the blocks below, Terraform would see the old
# resources as deleted from the configuration and run each one's `revert` —
# which for the databases is `DROP DATABASE`. It would drop the lakehouse.
#
# So: forget the old resources without touching Snowflake (`removed`), then
# adopt the objects they created into the new resources (`import`). The grants
# are neither removed nor imported by address — the old grant resources never
# made it into state (that is the apply that failed), and re-running a GRANT
# that already exists is a no-op in Snowflake, so the new grant resources can
# simply create themselves.
#
#   terraform plan    # expect: 0 to add for the volume/databases/schemas,
#                     #         16 to add for the grants, 0 to destroy
#   terraform apply
#   rm migrate-snowflake-execute.tf
#
# If `terraform state list | grep snowflake_execute` comes back empty, none of
# this is needed: delete the file now and apply.

removed {
  from = snowflake_execute.external_volume
  lifecycle { destroy = false }
}

removed {
  from = snowflake_execute.database
  lifecycle { destroy = false }
}

removed {
  from = snowflake_execute.namespace
  lifecycle { destroy = false }
}

removed {
  from = snowflake_execute.grant_external_volume
  lifecycle { destroy = false }
}

# The one that errored with "statement count 5 did not match 1". Present here in
# case a partial apply did land it in state; harmless if it did not.
removed {
  from = snowflake_execute.lakehouse_grants
  lifecycle { destroy = false }
}

# Import IDs are the provider's quoted fully-qualified names — the double quotes
# are part of the ID, not Terraform syntax.

import {
  to = snowflake_external_volume.lakehouse
  id = "\"${local.snowflake_external_volume}\""
}

import {
  for_each = local.snowflake_layer_databases

  to = snowflake_database.layer[each.key]
  id = "\"${each.value}\""
}

import {
  for_each = local.snowflake_namespaces

  to = snowflake_schema.namespace[each.key]
  id = "\"${local.snowflake_layer_databases[each.value.layer]}\".\"${each.value.schema}\""
}

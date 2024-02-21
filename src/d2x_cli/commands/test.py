import click
import json
from uuid import UUID
from snowfakery import generate_data
from d2x_cli.runtime import pass_runtime
from d2x_cli.api import get_d2x_api_client, D2XApiObjects, fk_field_to_model


@click.group("test", help="")
def test():
    """Top-level `click` command group for interacting with D2X GitHub repos."""
    pass


@click.command("seed-data", help="Seed data using Snowfakery and the D2X API.")
@click.option("--recipe", required=True, help="Path to the Snowfakery recipe file.")
@click.option(
    "--repo", required=True, help="The UUID of the repo in the D2X Cloud API."
)
@click.option("--output", default="output.json", help="Path to the output JSON file.")
@pass_runtime(require_project=False, require_keychain=True)
def seed_data(runtime, recipe, repo, output):
    """Generate and seed data using Snowfakery and the D2X API."""
    api_client = get_d2x_api_client(runtime)

    # Generate data with Snowfakery
    generate_data(recipe, output_file=output)

    # Read the generated data
    with open(output, "r") as f:
        data = json.load(f)

    id_map = {}

    # Post data to the API
    for record in data:
        obj_type = record["_table"]
        api_obj = D2XApiObjects[obj_type]

        # Prepare record data
        record_data = {k: v for k, v in record.items() if k not in ["id", "_table"]}

        # Replace internal IDs with UUIDs from the map
        for field, value in record_data.items():
            if field == "repo_id" and value == "GITHUB_REPO_ID":
                record_data[field] = repo
                continue
            if field.endswith("_id") and isinstance(value, int):
                model = fk_field_to_model(field)
                if model in id_map:
                    record_data[field] = id_map[model][value]

        parents = {}
        if obj_type == "PlanVersion":
            parents["plan_id"] = record_data["plan_id"]

        response = api_client.create(api_obj, json.dumps(record_data), parents=parents)
        data = response.json()
        print(f"Created {obj_type} with ID {data['id']}")

        # Store the mapping of internal ID to UUID
        id_map.setdefault(obj_type, {})[record["id"]] = data["id"]


test.add_command(seed_data)

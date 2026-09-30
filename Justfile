set positional-arguments

proxy-test:
    AWS_EC2_METADATA_DISABLED=true AWS_DEFAULT_REGION=us-east-2 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python -m unittest discover -s infra/lambda/google_proxy -p 'test_*.py'

# Import the existing canonical Web client; APIs/status must be checked by the operator first.
configure-google-application client_json confirmed_apis publishing_status:
    uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python scripts/configure_google_application.py --source "{{justfile_directory()}}" --environment gogcli-prd --client-json "$1" --confirmed-apis "$2" --publishing-status "$3"

operator-test:
    AWS_EC2_METADATA_DISABLED=true uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python -m unittest discover -s scripts -p 'test_configure_google_application.py'

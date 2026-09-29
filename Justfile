proxy-test:
    AWS_EC2_METADATA_DISABLED=true AWS_DEFAULT_REGION=us-east-2 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing uv run --python 3.11 --with boto3 --with-requirements infra/lambda/google_proxy/requirements.txt python -m unittest discover -s infra/lambda/google_proxy -p 'test_*.py'

# STUB. Real predict Lambda code (handler.py, event_prediction.py, etc.,
# mirroring Source/aws-lambdas/nfl/predict/) doesn't exist yet -- that's
# Sub-phase 3A step 6. This Dockerfile exists only so nba_ai_hosting.yml
# has something to build and push a valid `nba-predict-latest` image to
# ECR *before* tf_install.yml's terraform apply creates
# aws_lambda_function.nba_predict (a container-image Lambda -- CreateFunction
# validates the referenced image tag already exists, unlike a zip-packaged
# Lambda where Terraform can fabricate its own inline placeholder). Same
# role predict-read's aws_lambda_function.nba_predict_read placeholder ZIP
# plays for the zip-based Lambdas -- see lambda-nba-predict-read.tf.
#
# Replace this whole file with the real multi-stage build (copy
# Source/aws-lambdas/nfl/predict/Dockerfile's shape) at step 6 -- nothing
# else needs to change, nba_ai_hosting.yml already builds+pushes whatever
# is at this path.
FROM public.ecr.aws/lambda/python:3.12
COPY handler.py ${LAMBDA_TASK_ROOT}
CMD ["handler.lambda_handler"]

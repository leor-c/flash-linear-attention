FROM nvcr.io/nvidia/pytorch:25.02-py3
WORKDIR /FLA
RUN pip install --upgrade pip
RUN pip install --upgrade setuptools
RUN pip install triton
RUN pip install einops
RUN pip install transformers
RUN pip install datasets
RUN pip install causal-conv1d

RUN pip uninstall fla && pip install -U git+https://github.com/leor-c/flash-linear-attention


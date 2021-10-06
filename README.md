# bitsandbytes

bitsandbytes is a lightweight wrapper around CUDA custom functions, in particular 8-bit optimizers and quantization functions.

## Features
- 8-bit Optimizers: Adam, AdamW, RMSProp, LARS, LAMB
- Percentile clipping: A gradient clipping technique that adjusts dynamically for each weight-tensor during training
- Stable Embedding Layer: Improved stability through better initialization, and normalization
- Fast quantile estimation: Up to 100x faster than other algorithms
- 8-bit quantization: Quantile, Linear, and Dynamic quantization

#### Details
- **8-bit Optimizers** use an 8-bit instead of 32-bit state and thus save 75% of memory. 
- **Percentile Clipping** is an adaptive gradient clipping technique that adapts the clipping threshold automatically during training for each weight-tensor. It tracks a history of the past 100 gradient norms, and the gradient is clipped at a certain percentile p. For most tasks, p=5 works well and provides improved stability and, in some cases, even better performance (ResNet-50 ImageNet).
- The **Stable Embedding Layer** uses a less variable initialization coupled with layer norm for stability. Usually, dense optimizers are used in conjunction with sparse BPE/word embeddings, and these dense optimizers perform incorrect updates, leading to instability. The Stable Embedding Layer fixes this problem by performing sparse updates by default for any chosen bnb optimizer.
- Fast quantile estimation via **SRAM-Quantiles** algorithm, which is up to 100x faster than previous algorithms to estimate quantiles.
- Various **8-bit Quantization** schemes which are useful to compress data. For example, gradient communication or Mixture of Experts token routing can be improved by using 8-bit quantization before communication followed by decompression to 16/32-bit.

## Requirements & Installation

Requirements: anaconda, cudatoolkit, pytorch
Hardware requirements: NVIDIA Maxwell GPU or newer (>=GTX 9XX)

The requirements can best be fulfilled by installing pytorch via anaconda. You can install PyTorch by following the ["Get Started"](https://pytorch.org/get-started/locally/) instructions on the official website.

bitsandbytes is compatible with all major PyTorch releases and cudatoolkit versions, but for now, you need to select the right version manually. To do this run:

```conda list | grep cudatoolkit```

and take note of the Cuda version that you have installed. Then you can install bitsandbytes via:
```bash
# choices: {cuda92, cuda 100, cuda101, cuda102, cuda110, cuda111, cuda113}
# replace XXX with the respective number
pip install -i https://test.pypi.org/simple/ bitsandbytes-cudaXXX
```

To check if your installation was successful, you can execute the following command, which runs a single bnb Adam update.
```
wget https://gist.githubusercontent.com/TimDettmers/1f5188c6ee6ed69d211b7fe4e381e713/raw/4d17c3d09ccdb57e9ab7eca0171f2ace6e4d2858/check_bnb_install.py && python check_bnb_install.py
```

## Using bitsandbytes

### Using the 8-bit Optimizers

With bitsandbytes 8-bit optimizers can be used by changing a single line of code in your codebase. For NLP models we recommend also to use the StableEmbedding layers (see below) which improves results and helps with stable 8-bit optimization.  To get started with 8-bit optimizers, it is sufficient to replace your old optimizer with the 8-bit optimizer in the following way:
```python
import bitsandbytes as bnb

# adam = torch.optim.Adam(model.parameters(), lr=0.001, betas=(0.9, 0.995)) # comment out old optimizer
adam = bnb.optim.Adam8bit(model.parameters(), lr=0.001, betas=(0.9, 0.995)) # add bnb optimizer
adam = bnb.optim.Adam(model.parameters(), lr=0.001, betas=(0.9, 0.995), optim_bits=8) # equivalent

# use 32-bit Adam with 5th percentile clipping
adam = bnb.optim.Adam(model.parameters(), lr=0.001, betas=(0.9, 0.995),
                      optim_bits=32, percentile_clipping=5)
```

Note that by default all parameter tensors with less than 4096 elements are kept at 32-bit even if you initialize those parameters with 8-bit optimizers. This is done since such small tensors do not save much memory and often contain highly variable parameters (biases) or parameters that require high precision (batch norm, layer norm). 

### Change Bits and other Hyperparameters for Individual Parameters

If you want to optimize some unstable parameters with 32-bit Adam and others with 8-bit Adam, with can use the `GlobalOptimManager`. With this, we can also configure specific parameters for sparse optimization, such as embedding layers. To do that, we need two things: (1) register the parameter while they are still on the CPU, (2) override the config with the new desired hyperparameters (anytime, anywhere).

```python
import torch
import bitsandbytes as bnb

mng = bnb.optim.GlobalOptimManager.get_instance()

model = MyModel()
mng.register_parameters(model.parameters()) # 1. register parameters while still on CPU

model = model.cuda()
# use 8-bit optimizer states for all parameters
adam = bnb.optim.Adam(model.parameters(), lr=0.001, optim_bits=8) 

# 2a. override: the parameter model.fc1.weight now uses 32-bit Adam
mng.override_config(model.fc1.weight, 'optim_bits', 32) 

# 2b. override: the two special layers use
# sparse optimization + different learning rate + different Adam betas
mng.override_config([model.special.weight, model.also_special.weight],
                    key_value_dict ={'is_sparse': True, 'lr': 1e-5, 'betas'=(0.9, 0.98)}) 
``` 

### Stable Embedding Layer

To use the stable embedding layer, simply replace the PyTorch embedding layer with `bnb.nn.StableEmbedding`. By default, this layer is sparsely optimized.

### Fairseq Users

To use the Stable Embedding Layer, override the respective `build_embedding(...)` function of your model. Make sure to also use the `--no-scale-embedding` flag to disable scaling of the word embedding layer (nor replaced with layer norm). You can use the optimizers by replacing the optimizer in the respective file (`adam.py` etc.).

## Release and Feature History

Last release: v0.0.22:
- Fixed an error where a `reset_parameters()` call on the `StableEmbedding` would lead to an error in older PyTorch versions (from 1.7.0).

For upcoming features and changes and full history see [Patch Notes](PATCH_NOTES.md).

## License

The majority of bitsandbytes is licensed under MIT, however portions of the project are available under separate license terms: Pytorch is licensed under the BSD license.

We thank Fabio Cannizzo for his work on [FastBinarySearch](https://github.com/fabiocannizzo/FastBinarySearch) which we use for CPU quantization.

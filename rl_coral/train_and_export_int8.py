"""Train the residual gait policy with PPO and export it as a full-int8 TFLite model.

    python -m rl_coral.train_and_export_int8 --timesteps 2000000 --envs 8
    python -m rl_coral.train_and_export_int8 --export-only     # reuse models/gait_policy_ppo.zip

Steps: PPO on PiCrawlerVisionEnv -> identical tf.keras MLP with the actor
weights copied over -> checked against PyTorch -> full-integer quantization
with 1000 rollout observations as representative data -> int8 model checked
against the float model -> edgetpu_compiler, if it is installed (it only
exists for x86-64 Linux; otherwise the command to run is printed).
"""
import argparse
import os
import shutil
import subprocess
import sys

import numpy as np

from rl_coral import common

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "models")
HIDDEN = [64, 64]


def make_env(seed):
    def factory():
        from rl_coral.sim_env import PiCrawlerVisionEnv
        return PiCrawlerVisionEnv(seed=seed)
    return factory


def train(args, zip_path):
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.monitor import Monitor
    from stable_baselines3.common.vec_env import DummyVecEnv, SubprocVecEnv, VecMonitor

    factories = [make_env(args.seed + i) for i in range(args.envs)]
    env = SubprocVecEnv(factories) if args.envs > 1 else DummyVecEnv([lambda: Monitor(factories[0]())])
    if args.envs > 1:
        env = VecMonitor(env)
    model = PPO(
        "MlpPolicy", env,
        policy_kwargs=dict(activation_fn=torch.nn.ReLU, net_arch=dict(pi=HIDDEN, vf=HIDDEN)),
        n_steps=args.n_steps, batch_size=args.batch_size, n_epochs=10, learning_rate=3e-4,
        gamma=0.99, gae_lambda=0.95, clip_range=0.2, ent_coef=0.0, seed=args.seed,
        device="cpu", verbose=1,
    )
    checkpoints = CheckpointCallback(save_freq=max(1, 200_000 // args.envs), save_path=os.path.join(args.out, "checkpoints"),
                                     name_prefix="gait_policy")
    model.learn(total_timesteps=args.timesteps, callback=checkpoints, progress_bar=False)
    model.save(zip_path)
    env.close()
    print("saved %s" % zip_path)
    return model


def torch_actor(policy):
    """[(W, b), ...] of the actor in order: two hidden layers, then action_net."""
    layers = [m for m in policy.mlp_extractor.policy_net if hasattr(m, "weight")] + [policy.action_net]
    if [tuple(l.weight.shape) for l in layers] != [(HIDDEN[0], common.OBS_DIM), (HIDDEN[1], HIDDEN[0]),
                                                   (common.ACT_DIM, HIDDEN[1])]:
        raise ValueError("unexpected actor shape: %s" % [tuple(l.weight.shape) for l in layers])
    return [(l.weight.detach().cpu().numpy(), l.bias.detach().cpu().numpy()) for l in layers]


def build_keras(weights):
    import tensorflow as tf
    keras = tf.keras
    inputs = keras.Input(shape=(common.OBS_DIM,), batch_size=1, name="observation")
    x = keras.layers.Dense(HIDDEN[0], activation="relu", name="pi_0")(inputs)
    x = keras.layers.Dense(HIDDEN[1], activation="relu", name="pi_1")(x)
    outputs = keras.layers.Dense(common.ACT_DIM, activation="tanh", name="action")(x)
    model = keras.Model(inputs, outputs, name="gait_policy")
    for layer, (w, b) in zip(model.layers[1:], weights):
        layer.set_weights([w.T, b])          # torch Linear stores (out, in), Dense wants (in, out)
    return model


def torch_reference(policy, obs):
    import torch
    with torch.no_grad():
        latent = policy.mlp_extractor.policy_net(torch.as_tensor(obs, dtype=torch.float32))
        return torch.tanh(policy.action_net(latent)).numpy()


def rollout_observations(model, count, seed):
    """Observations from the trained policy acting in the randomized sim."""
    from rl_coral.sim_env import PiCrawlerVisionEnv
    env = PiCrawlerVisionEnv(seed=seed)
    obs, _ = env.reset(seed=seed)
    samples = []
    while len(samples) < count:
        samples.append(obs)
        action, _ = model.predict(obs, deterministic=True)
        obs, _, terminated, truncated, _ = env.step(action)
        if terminated or truncated:
            obs, _ = env.reset()
    env.close()
    return np.array(samples, np.float32)


def convert_int8(keras_model, samples, path):
    import tensorflow as tf

    def representative_dataset():
        for obs in samples:
            yield [obs.reshape(1, common.OBS_DIM).astype(np.float32)]

    # The input has batch size 1, so the model's signature is (1, 32) -> (1, 12).
    converter = tf.lite.TFLiteConverter.from_keras_model(keras_model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    converter.representative_dataset = representative_dataset
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    data = converter.convert()
    with open(path, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes)" % (path, len(data)))


def check_int8(path, keras_model, samples):
    """Max / mean error of the int8 model against float, in mm of foot offset."""
    import tensorflow as tf
    interpreter = tf.lite.Interpreter(model_path=path, experimental_op_resolver_type=
                                      tf.lite.experimental.OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
    interpreter.allocate_tensors()
    inp, out = interpreter.get_input_details()[0], interpreter.get_output_details()[0]
    if inp["dtype"] != np.int8 or out["dtype"] != np.int8:
        raise RuntimeError("model is not int8 in/out: %s / %s" % (inp["dtype"], out["dtype"]))
    in_scale, in_zero = inp["quantization"]
    out_scale, out_zero = out["quantization"]
    errors = []
    for obs in samples[:300]:
        interpreter.set_tensor(inp["index"], common.quantize(obs.reshape(1, -1), in_scale, in_zero))
        interpreter.invoke()
        q = common.dequantize(interpreter.get_tensor(out["index"]), out_scale, out_zero)
        ref = keras_model(obs.reshape(1, -1), training=False).numpy()
        errors.append(np.abs(q - ref).max())
    ops = sorted({op["op_name"] for op in interpreter._get_ops_details()}) if hasattr(interpreter, "_get_ops_details") else []
    print("int8 vs float: max %.2f mm, mean %.2f mm over %d samples; input scale %.5f zp %d; ops %s"
          % (max(errors) * common.OFFSET_MM, np.mean(errors) * common.OFFSET_MM, len(errors),
             in_scale, in_zero, ", ".join(ops) or "?"))
    return max(errors) * common.OFFSET_MM


def compile_edgetpu(path, out_dir):
    compiler = shutil.which("edgetpu_compiler")
    command = ["edgetpu_compiler", "-s", "-o", out_dir, path]
    if compiler is None:
        print("edgetpu_compiler not found (it runs on x86-64 Linux only). Compile with:\n  %s\n"
              "Expected output: %s" % (" ".join(command), path.replace(".tflite", "_edgetpu.tflite")))
        return None
    result = subprocess.run(command, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("edgetpu_compiler failed")
    if "will run on CPU" in result.stdout and "0 operation(s) will run on CPU" not in result.stdout:
        print("WARNING: some operations fall back to the CPU, see the log above", file=sys.stderr)
    compiled = os.path.join(out_dir, os.path.basename(path).replace(".tflite", "_edgetpu.tflite"))
    print("wrote %s" % compiled)
    return compiled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--timesteps", type=int, default=2_000_000)
    parser.add_argument("--envs", type=int, default=8)
    parser.add_argument("--n-steps", type=int, default=1024, help="rollout length per env")
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--samples", type=int, default=1000, help="representative observations")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--export-only", action="store_true", help="skip training, load gait_policy_ppo.zip")
    args = parser.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, "gait_policy_ppo.zip")
    if args.export_only:
        from stable_baselines3 import PPO
        model = PPO.load(zip_path, device="cpu")
    else:
        model = train(args, zip_path)

    keras_model = build_keras(torch_actor(model.policy))
    samples = rollout_observations(model, args.samples, args.seed + 1000)
    diff = np.abs(keras_model(samples, training=False).numpy() - torch_reference(model.policy, samples)).max()
    print("keras vs torch: max difference %.2e" % diff)
    if diff > 1e-4:
        raise RuntimeError("weight copy is wrong (difference %.2e)" % diff)
    np.save(os.path.join(args.out, "representative_obs.npy"), samples)

    tflite_path = os.path.join(args.out, "gait_policy_int8.tflite")
    convert_int8(keras_model, samples, tflite_path)
    check_int8(tflite_path, keras_model, samples)
    compile_edgetpu(tflite_path, args.out)


if __name__ == "__main__":
    main()

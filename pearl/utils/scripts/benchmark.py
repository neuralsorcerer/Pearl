# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#

"""
The code in this file provides a way to run multiple pearl experiments in different
processes using torch.multiprocessing.
Outputs of the code are saved in the folder ~/pearl_execution/outputs/.
To run the code, enter the pearl directory, then run
./utils/scripts/meta_only/run_pearl.sh utils/scripts/benchmark_parallelization.py
(make sure conda and related packages have been installed with
./utils/scripts/meta_only/setup_conda_pearl_on_devserver.sh)
"""

import os
import warnings

import ale_py
import matplotlib.pyplot as plt
import numpy as np
import torch.multiprocessing as mp
from pearl.action_representation_modules.identity_action_representation_module import (
    IdentityActionRepresentationModule,
)
from pearl.neural_networks.common.value_networks import CNNValueNetwork
from pearl.neural_networks.sequential_decision_making.actor_networks import (
    CNNActorNetwork,
)
from pearl.neural_networks.sequential_decision_making.q_value_networks import (
    CNNQValueMultiHeadNetwork,
    CNNQValueNetwork,
)
from pearl.neural_networks.sequential_decision_making.twin_critic import TwinCritic
from pearl.pearl_agent import PearlAgent
from pearl.utils.functional_utils.experimentation.set_seed import set_seed

from pearl.utils.functional_utils.train_and_eval.online_learning import online_learning
from pearl.utils.scripts.benchmark_config import (  # noqa: F401
    benchmark_acrobot_v1_part_1,
    benchmark_acrobot_v1_part_2,
    benchmark_ant_v4,
    benchmark_atari,
    benchmark_cartpole_v1_part_1,
    benchmark_cartpole_v1_part_2,
    benchmark_halfcheetah_v4,
    benchmark_hopper_v4,
    benchmark_pendulum_v1_lstm,
    benchmark_walker2d_v4,  # noqa: F401
    get_env,
    rccsac_ant,
    rccsac_half_cheetah,
    rccsac_hopper,
    rccsac_walker,
    rcddpg_ant,
    rcddpg_half_cheetah,
    rcddpg_hopper,
    rcddpg_walker,
    rctd3_ant,
    rctd3_half_cheetah,
    rctd3_hopper,
    rctd3_walker,
    test_dynamic_action_space,
)

warnings.filterwarnings("ignore")
attr_to_title = {
    "return": "return",
    "return_cost": "cummulative cost",
    "risk_sa": "risk_sa",
}


def run(experiments) -> None:
    """Assign one run to one process."""
    assert len(experiments) > 0
    all_processes = []

    for e in experiments:
        evaluate(e, all_processes)

    for p in all_processes:
        p.start()
    for p in all_processes:
        p.join()


def evaluate(experiment, all_processes: list[mp.Process]) -> None:
    """Running multiple methods and multiple runs in the given gym environment."""
    env_name = experiment["env_name"]
    num_runs = experiment["num_runs"]
    num_episodes = experiment.get("num_episodes")
    num_steps = experiment.get("num_steps")
    record_period = experiment["record_period"]
    print_every_x_episodes = experiment.get("print_every_x_episodes")
    print_every_x_steps = experiment.get("print_every_x_steps")
    methods = experiment["methods"]
    processes = []
    for method in methods:
        method["agent_args"] = {"device_id": experiment["device_id"]}
        for run_idx in range(num_runs):
            p = mp.Process(
                target=evaluate_single,
                args=(
                    env_name,
                    method,
                    run_idx,
                    num_episodes,
                    num_steps,
                    print_every_x_episodes,
                    print_every_x_steps,
                    record_period,
                ),
            )
            processes.append(p)

    all_processes.extend(processes)


def evaluate_single(
    env_name,
    method,
    run_idx,
    num_episodes,
    num_steps,
    print_every_x_episodes,
    print_every_x_steps,
    record_period,
):
    """Performing one run of experiment."""
    set_seed(run_idx)
    policy_learner = method["policy_learner"]
    policy_learner_args = method["policy_learner_args"]
    agent_args = method["agent_args"]
    env = get_env(env_name)
    policy_learner_args["state_dim"] = env.observation_space.shape[0]

    if "exploration_module" in method and "exploration_module_args" in method:
        policy_learner_args["exploration_module"] = method["exploration_module"](
            **method["exploration_module_args"]
        )
        if "exploration_module_wrapper" in method:
            policy_learner_args["exploration_module"] = method[
                "exploration_module_wrapper"
            ](
                exploration_module=policy_learner_args["exploration_module"],
                **method["exploration_module_wrapper_args"],
            )
    if "replay_buffer" in method and "replay_buffer_args" in method:
        agent_args["replay_buffer"] = method["replay_buffer"](
            **method["replay_buffer_args"]
        )
    if "safety_module" in method and "safety_module_args" in method:
        agent_args["safety_module"] = method["safety_module"](
            **method["safety_module_args"]
        )
    if (
        "action_representation_module" in method
        and "action_representation_module_args" in method
    ):
        if method["action_representation_module"].__name__ in [
            "OneHotActionTensorRepresentationModule",
        ]:
            method["action_representation_module_args"]["max_number_actions"] = (
                env.action_space.n
            )
        if method["action_representation_module"].__name__ in [
            "IdentityActionRepresentationModule"
        ]:
            method["action_representation_module_args"]["max_number_actions"] = (
                env.action_space.n
            )
            method["action_representation_module_args"]["representation_dim"] = (
                env.action_space.action_dim
            )
        policy_learner_args["action_representation_module"] = method[
            "action_representation_module"
        ](**method["action_representation_module_args"])

    else:
        policy_learner_args["action_representation_module"] = (
            IdentityActionRepresentationModule()
        )

    if (
        "history_summarization_module" in method
        and "history_summarization_module_args" in method
    ):
        if (
            method["history_summarization_module"].__name__
            == "StackingHistorySummarizationModule"
        ):
            method["history_summarization_module_args"]["observation_dim"] = (
                env.observation_space.shape[0]
            )
            method["history_summarization_module_args"]["action_dim"] = (
                policy_learner_args["action_representation_module"].representation_dim
                if "action_representation_module" in policy_learner_args
                else env.action_space.action_dim
            )
            policy_learner_args["state_dim"] = (
                method["history_summarization_module_args"]["observation_dim"]
                + method["history_summarization_module_args"]["action_dim"]
            ) * method["history_summarization_module_args"]["history_length"]
        elif (
            method["history_summarization_module"].__name__
            == "LSTMHistorySummarizationModule"
        ):
            method["history_summarization_module_args"]["observation_dim"] = (
                env.observation_space.shape[0]
            )
            method["history_summarization_module_args"]["action_dim"] = (
                policy_learner_args["action_representation_module"].representation_dim
                if "action_representation_module" in policy_learner_args
                else env.action_space.action_dim
            )
            policy_learner_args["state_dim"] = method[
                "history_summarization_module_args"
            ]["hidden_dim"]

        agent_args["history_summarization_module"] = method[
            "history_summarization_module"
        ](**method["history_summarization_module_args"])

    if "network_module" in method and method["network_module"] in [
        CNNQValueNetwork,
        CNNQValueMultiHeadNetwork,
    ]:
        policy_learner_args["network_instance"] = method["network_module"](
            input_width=env.observation_space.shape[2],
            input_height=env.observation_space.shape[1],
            input_channels_count=env.observation_space.shape[0],
            action_dim=policy_learner_args[
                "action_representation_module"
            ].representation_dim,
            output_dim=(
                1
                if method["network_module"] is CNNQValueNetwork
                else policy_learner_args[
                    "action_representation_module"
                ].representation_dim
            ),
            **method["network_args"],
        )
    if "critic_network_module" in method and method["critic_network_module"] in [
        CNNQValueNetwork,
        CNNQValueMultiHeadNetwork,
    ]:
        action_dim = policy_learner_args[
            "action_representation_module"
        ].representation_dim
        output_dim = (
            1 if method["critic_network_module"] is CNNQValueNetwork else action_dim
        )
        if "use_twin_critic" in method and method["use_twin_critic"]:
            policy_learner_args["critic_network_instance"] = TwinCritic(
                network_instance_1=method["critic_network_module"](
                    input_width=env.observation_space.shape[2],
                    input_height=env.observation_space.shape[1],
                    input_channels_count=env.observation_space.shape[0],
                    action_dim=action_dim,
                    output_dim=output_dim,
                    **method["critic_network_args"],
                ),
                network_instance_2=method["critic_network_module"](
                    input_width=env.observation_space.shape[2],
                    input_height=env.observation_space.shape[1],
                    input_channels_count=env.observation_space.shape[0],
                    action_dim=action_dim,
                    output_dim=output_dim,
                    **method["critic_network_args"],
                ),
            )
        else:
            policy_learner_args["critic_network_instance"] = method[
                "critic_network_module"
            ](
                input_width=env.observation_space.shape[2],
                input_height=env.observation_space.shape[1],
                input_channels_count=env.observation_space.shape[0],
                action_dim=action_dim,
                output_dim=output_dim,
                **method["critic_network_args"],
            )

    if (
        "critic_network_module" in method
        and method["critic_network_module"] is CNNValueNetwork
    ):
        policy_learner_args["critic_network_instance"] = method[
            "critic_network_module"
        ](
            input_width=env.observation_space.shape[2],
            input_height=env.observation_space.shape[1],
            input_channels_count=env.observation_space.shape[0],
            output_dim=1,
            **method["critic_network_args"],
        )

    if (
        "actor_network_module" in method
        and method["actor_network_module"] is CNNActorNetwork
    ):
        policy_learner_args["actor_network_instance"] = method["actor_network_module"](
            input_width=env.observation_space.shape[2],
            input_height=env.observation_space.shape[1],
            input_channels_count=env.observation_space.shape[0],
            output_dim=policy_learner_args[
                "action_representation_module"
            ].representation_dim,
            **method["actor_network_args"],
        )

    if method["name"] == "DuelingDQN":  # only for Dueling DQN
        assert "network_module" in method and "network_args" in method
        policy_learner_args["network_instance"] = method["network_module"](
            state_dim=env.observation_space.shape[0],
            action_dim=env.action_space.n,
            **method["network_args"],
        )
    if method["name"] == "BootstrappedDQN":  # only for Bootstrapped DQN
        assert "network_module" in method and "network_args" in method
        policy_learner_args["q_ensemble_network"] = method["network_module"](
            state_dim=env.observation_space.shape[0],
            action_dim=env.action_space.n,
            **method["network_args"],
        )
        del policy_learner_args["state_dim"]

    if "dynamic" in method["name"]:
        policy_learner_args["actor_network_type"] = method["actor_network_type"]

    policy_learner_args["action_space"] = env.action_space
    agent = PearlAgent(
        policy_learner=policy_learner(
            **policy_learner_args,
        ),
        **agent_args,
    )
    method_name = method["name"]
    print(f"Run #{run_idx + 1} for {method_name} in {env_name}")
    learn_every_k_steps = method.get("learn_every_k_steps", 1)
    learn_after_episode = method.get("learn_after_episode", False)
    info = online_learning(
        agent,
        env,
        number_of_episodes=num_episodes,
        number_of_steps=num_steps,
        print_every_x_episodes=print_every_x_episodes,
        print_every_x_steps=print_every_x_steps,
        learn_after_episode=learn_after_episode,
        learn_every_k_steps=learn_every_k_steps,
        seed=run_idx,
        record_period=record_period,
        learning_start_step=method.get("learning_start_step", 0),
    )
    dir = f"outputs/{env_name}/{method_name}"
    os.makedirs(dir, exist_ok=True)
    for key in info:
        np.save(dir + f"/{run_idx}_{key}.npy", info[key])


def generate_plots(experiments, attributes) -> None:
    for e in experiments:
        generate_one_plot(e, attributes)


def generate_one_plot(experiment, attributes):
    """Generating learning curves for all tested methods in one environment."""
    plt.rcParams.update({"font.size": 15})
    env_name = experiment["env_name"]
    exp_name = experiment["exp_name"]
    num_runs = experiment["num_runs"]
    record_period = experiment["record_period"]
    methods = experiment["methods"]
    for attr in attributes:
        for method in methods:
            data = []
            for run in range(num_runs):
                try:
                    d = np.load(f"outputs/{env_name}/{method['name']}/{run}_{attr}.npy")
                except FileNotFoundError:
                    print(
                        f"File not found for outputs/{env_name}/{method['name']}/{run}_{attr}.npy"
                    )
                    continue
                data.append(d)
            data = np.array(data)
            mean = data.mean(axis=0)
            std_error = data.std(axis=0) / np.sqrt(num_runs)
            x_list = record_period * np.arange(mean.shape[0])
            if "num_steps" in experiment:
                plt.plot(x_list, mean, label=method["name"])
                plt.fill_between(x_list, mean - std_error, mean + std_error, alpha=0.2)
            else:
                plt.plot(x_list, mean, label=method["name"])
                plt.fill_between(
                    x_list,
                    mean - std_error,
                    mean + std_error,
                    alpha=0.2,
                )
        plt.title(env_name.replace("_", "-"))
        plt.ticklabel_format(style="sci", axis="y", scilimits=(0, 0))
        plt.ticklabel_format(style="sci", axis="x", scilimits=(0, 0))
        if "num_steps" in experiment:
            plt.xlabel("Steps")
        else:
            plt.xlabel("Episodes")
        plt.ylabel(attr_to_title[attr])
        plt.legend()
        plt.savefig(f"outputs/{exp_name}_{env_name}_{attr}.png")
        plt.close()


if __name__ == "__main__":
    run(benchmark_atari)
    # run(benchmark_pendulum_v1_lstm)
    # generate_plots(benchmark_pendulum_v1_lstm, ["return"])
    # run(benchmark_pendulum_v1_lstm2)
    # generate_plots(benchmark_pendulum_v1_lstm2, ["return"])
    # run(benchmark_pendulum_v1_lstm3)
    # generate_plots(benchmark_pendulum_v1_lstm3, ["return"])
    # run(benchmark_pendulum_v1_lstm4)
    # generate_plots(benchmark_pendulum_v1_lstm4, ["return"])
    # run(benchmark_cartpole_v1_part_1)
    # generate_plots(benchmark_cartpole_v1_part_1, ["return"])
    # run(benchmark_cartpole_v1_part_2)
    # generate_plots(benchmark_cartpole_v1_part_2, ["return"])
    # run(benchmark_cartpole_v1_part_3)
    # generate_plots(benchmark_cartpole_v1_part_3, ["return"])
    # run(benchmark_acrobot_v1_part_1)
    # generate_plots(benchmark_acrobot_v1_part_1, ["return"])
    # run(benchmark_acrobot_v1_part_2)
    # generate_plots(benchmark_acrobot_v1_part_2, ["return"])
    # run(benchmark_halfcheetah_v4)
    # generate_plots(benchmark_halfcheetah_v4, ["return"])
    # run(benchmark_ant_v4)
    # generate_plots(benchmark_ant_v4, ["return"])
    # run(benchmark_hopper_v4)
    # generate_plots(benchmark_hopper_v4, ["return"])
    # run(benchmark_walker2d_v4)
    # generate_plots(benchmark_walker2d_v4, ["return"])

    # test dynamic action spaces
    # run(test_dynamic_action_space)
    # generate_plots(test_dynamic_action_space, ["return"])

    # test reward constraint versions of algorithms
    # run(rcddpg_ant)
    # generate_plots(rcddpg_ant, ["return", "return_cost"])
    # run(rcddpg_half_cheetah)
    # generate_plots(rcddpg_half_cheetah, ["return", "return_cost"])
    # run(rcddpg_hopper)
    # generate_plots(rcddpg_hopper, ["return", "return_cost"])
    # run(rcddpg_walker)
    # generate_plots(rcddpg_walker, ["return", "return_cost"])
    # run(rctd3_ant)
    # generate_plots(rctd3_ant, ["return", "return_cost"])
    # run(rctd3_half_cheetah)
    # generate_plots(rctd3_half_cheetah, ["return", "return_cost"])
    # run(rctd3_hopper)
    # generate_plots(rctd3_hopper, ["return", "return_cost"])
    # run(rctd3_walker)
    # generate_plots(rctd3_walker, ["return", "return_cost"])
    # run(rccsac_ant)
    # generate_plots(rccsac_ant, ["return", "return_cost"])
    # run(rccsac_half_cheetah)
    # generate_plots(rccsac_half_cheetah, ["return", "return_cost"])
    # run(rccsac_hopper)
    # generate_plots(rccsac_hopper, ["return", "return_cost"])
    # run(rccsac_walker)
    # generate_plots(rccsac_walker, ["return", "return_cost"])
    # test dynamic action spaces
    # run(test_dynamic_action_space)
    # generate_plots(test_dynamic_action_space, ["return"])

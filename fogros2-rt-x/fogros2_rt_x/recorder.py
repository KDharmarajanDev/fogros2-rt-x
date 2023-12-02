# Copyright 2022 The Regents of the University of California (Regents)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Copyright ©2022. The Regents of the University of California (Regents).
# All Rights Reserved. Permission to use, copy, modify, and distribute this
# software and its documentation for educational, research, and not-for-profit
# purposes, without fee and without a signed licensing agreement, is hereby
# granted, provided that the above copyright notice, this paragraph and the
# following two paragraphs appear in all copies, modifications, and
# distributions. Contact The Office of Technology Licensing, UC Berkeley, 2150
# Shattuck Avenue, Suite 510, Berkeley, CA 94720-1620, (510) 643-7201,
# otl@berkeley.edu, http://ipira.berkeley.edu/industry-info for commercial
# licensing opportunities. IN NO EVENT SHALL REGENTS BE LIABLE TO ANY PARTY
# FOR DIRECT, INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES,
# INCLUDING LOST PROFITS, ARISING OUT OF THE USE OF THIS SOFTWARE AND ITS
# DOCUMENTATION, EVEN IF REGENTS HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH
# DAMAGE. REGENTS SPECIFICALLY DISCLAIMS ANY WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
# PARTICULAR PURPOSE. THE SOFTWARE AND ACCOMPANYING DOCUMENTATION, IF ANY,
# PROVIDED HEREUNDER IS PROVIDED "AS IS". REGENTS HAS NO OBLIGATION TO PROVIDE
# MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS, OR MODIFICATIONS.

import socket

import rclpy
from rclpy.node import Node
from .dataset_utils import *
from fogros2_rt_x_msgs.msg import Step, Observation, Action
import envlogger
from envlogger import step_data
import tensorflow_datasets as tfds
from envlogger.backends import tfds_backend_writer


# code borrowed from https://github.com/rail-berkeley/oxe_envlogger/blob/main/oxe_envlogger/dm_env.py
import dm_env
from dm_env import specs
class DummyDmEnv():
    """
    This is a dummy class to use so that the dm_env.Environment interface
    can still receive the observation_space and action_space. So that the
    logging can be done in the dm_env.Environment interface.

    https://github.com/google-deepmind/dm_env
    """

    def __init__(self,
                 observation_space,
                 action_space,
                 step_callback,
                 reset_callback,
                 ):
        """
        :param observation_space: gym observation space
        :param action_space: gym action space
        :param step_callback: callback function to call gymenv.step
        :param reset_callback: callback function to call gymenv.reset
        """
        self.step_callback = step_callback
        self.reset_callback = reset_callback
        self.observation_space = observation_space
        self.action_space = action_space

    def step(self, action) -> dm_env.TimeStep:
        # Note that dm_env.step doesn't accept additional arguments
        val = self.step_callback(action)
        obs, reward, terminate, truncate, info = val
        reward = float(reward)
        if terminate:
            ts = dm_env.termination(reward=reward, observation=obs)
        elif truncate:
            ts = dm_env.truncation(reward=reward, observation=obs)
        else:
            ts = dm_env.transition(reward=reward, observation=obs)
        return ts

    def reset(self) -> dm_env.TimeStep:
        # Note that dm_env.reset doesn't accept additional arguments
        obs, _ = self.reset_callback()
        ts = dm_env.restart(obs)
        return ts

    # def cast_tf_datatype_to_numpy(self, tf_datatype):
    #     # TODO: placeholder
    #     if tf_datatype == tf.float32:
    #         return np.float32
    #     elif tf_datatype == tf.float64:
    #         return np.float64
    #     elif tf_datatype == tf.string:
    #         return np.str
    #     else:
    #         raise NotImplementedError
    
    def cast_tf_datatype_to_numpy(self, tf_datatype):
        return tf_datatype.as_numpy_dtype
    
    def from_tf_feature_to_spec(self, feature) -> specs:
        print(feature)
        spec = {
            key: specs.Array(
                dtype=self.cast_tf_datatype_to_numpy(space.dtype),
                shape=space.shape,
                name=key,
            )
            for key, space in feature.items() if space.dtype != tf.string
        }

        return spec

    def observation_spec(self):
        return self.from_tf_feature_to_spec({
                        # 'image': tfds.features.Image(shape=(480, 640, 3), dtype=tf.uint8),
                        'natural_language_embedding': tfds.features.Tensor(shape=(512,), dtype=tf.float32),
                        # 'natural_language_instruction': tf.string,
                        'state': tfds.features.Tensor(shape=(7,), dtype=tf.float32),
                    })

    def action_spec(self):
        return self.from_tf_feature_to_spec({
                        # 'open_gripper': tf.bool,
                        'rotation_delta':  tfds.features.Tensor(shape=(3,), dtype=tf.float32),
                        # 'terminate_episode': tf.float32,
                        'world_vector': tfds.features.Tensor(shape=(3,), dtype=tf.float32),
                    })

    def reward_spec(self):
        return specs.Array(
            shape=(),
            dtype=np.float32,
            name='reward',
        )

    def discount_spec(self):
        return specs.Array(
            shape=(),
            dtype=np.float32,
            name='discount',
        )


class DatasetRecorder(Node):
    def __init__(self):
        super().__init__("fogros2_rt_x_recorder")

        self.observation_spec = tfds.features.FeaturesDict({
                        # 'image': tfds.features.Image(shape=(480, 640, 3), dtype=tf.uint8),
                        'natural_language_embedding': tfds.features.Tensor(shape=(512,), dtype=tf.float32),
                        # 'natural_language_instruction': tf.string,
                        'state': tfds.features.Tensor(shape=(7,), dtype=tf.float32),
                    })
        self.action_spec = tfds.features.FeaturesDict({
                        # 'open_gripper': tf.bool,
                        'rotation_delta':  tfds.features.Tensor(shape=(3,), dtype=tf.float32),
                        # 'terminate_episode': tf.float32,
                        'world_vector': tfds.features.Tensor(shape=(3,), dtype=tf.float32),
                    })
        dataset_config = tfds.rlds.rlds_base.DatasetConfig(
            name='bridge',
            observation_info=self.observation_spec,
            action_info=self.action_spec,
            reward_info=tf.float64,
            discount_info=tf.float64,
            step_metadata_info={'is_first': tf.bool, 'is_last': tf.bool, 'is_terminal': tf.bool})
        
        self.last_action = None 
        self.last_observation = None 
        self.last_reward = 0.0
        self.last_is_terminal = False

        def step_callback(action):
            # return value is 
            # observation, reward, is_terminal, is_truncated, info
            return (self.last_observation, self.last_reward, self.last_is_terminal, False, {})
                #(np.zeros((512,)), 0.0, True, False, {})

        def reset_callback():
            return (np.zeros((512,)), {})
            # return self.env.reset()
        
        self.env = DummyDmEnv(
            observation_space=None, # TODO: spec above
            action_space=None, # TODO: spec above 
            step_callback=step_callback,
            reset_callback=reset_callback,
        )

        self.writer = tfds_backend_writer.TFDSBackendWriter(
                data_directory="/home/ubuntu/open-x-embodiment/playground_ds",
                split_name='train',
                max_episodes_per_file=1,
                ds_config=dataset_config)

        self.envlogger = envlogger.EnvLogger(
            self.env,
            backend = self.writer
        )
        self.subscription = self.create_subscription(
            Step, "step_topic", self.listener_callback, 10
        )
        self.subscription  # prevent unused variable warning

    def convert_ros2_msg_to_tf_feature(self, ros2_msg):
        observation = dict()
        action = dict()
        #TODO: assume type conversion here
        for k, v in self.observation_spec.items():
            observation[k] = list(getattr(ros2_msg.observation, k))
        for k, v in self.action_spec.items():
            action[k] = list(getattr(ros2_msg.action, k))
        reward = float(ros2_msg.reward)
        discount = 1.0 #ros2_msg.discount
        is_first = ros2_msg.is_first
        is_last = ros2_msg.is_last
        is_terminal = ros2_msg.is_terminal
        return observation, action, reward, discount, is_first, is_last, is_terminal
    
    def listener_callback(self, step_msg):
        self.get_logger().warning(
            f"Received step: {str(step_msg)[:100]}"
        )

        self.last_observation, self.last_action, self.last_reward, discount, is_first, is_last, self.last_is_terminal = self.convert_ros2_msg_to_tf_feature(step_msg)
        # self.envlogger.step(
        #     self.last_action
        # )
        timestep = dm_env.TimeStep(
            step_type=dm_env.StepType.FIRST if is_first else dm_env.StepType.MID,
            reward=self.last_reward,
            #TODO: support discount
            discount=discount,
            observation=self.last_observation,
        )

        data = step_data.StepData(timestep = timestep, 
                                  action = self.last_action, 
                                  custom_data = None)
        if is_first:
            self.writer.record_step(data, is_new_episode=True)
        else:
            self.writer.record_step(data, is_new_episode=False)
        


        # self.envlogger.log_step(
        #     observation=step_msg.observation,
        #     action=step_msg.action,
        #     reward=step_msg.reward,
        #     discount=step_msg.discount,
        #     step_metadata=step_msg.step_metadata,
        # )


def main(args=None):
    rclpy.init(args=args)

    dataset_recorder = DatasetRecorder()

    rclpy.spin(dataset_recorder)

    # Destroy the node explicitly
    # (optional - otherwise it will be done automatically
    # when the garbage collector destroys the node object)
    dataset_recorder.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()

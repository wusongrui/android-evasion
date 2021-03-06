import random
import re, string
import contextlib
import sys
import logging
import subprocess
import copy
import math
import numpy as np
from multiprocessing import Pool
from util import util
from lib import liblinearutil

sample_size = 40

benign_pool = list()
benign_pool_file = "seeds/training_all.benign"
benign_pool_size = 0

# seed_file = "seeds/testing_1.seeds"
# seed_file = "seeds/testing_10.seeds"
seed_file = "seeds/testing_500.seeds"
model_name = "Marvin/models/model_all_liblinear-L2"

mutation_rate = 0.5
number_unfit = int(sample_size*mutation_rate)

header = "500_"

def setup_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler("output/logs/" + name + ".log")
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)
    return logger

std_logger = setup_logger("gp/" + header + "master")
feature_logger = setup_logger("gp/" + header + "features")
evasion_logger = setup_logger("gp/" + header+ "evasive")

def init():
    global benign_pool, benign_pool_size, model
    print("Initializing...")
    random.seed(1)
    model = liblinearutil.load_model(model_name)

    # Load gene pool
    std_logger.debug("Loading gene pool...")
    with open(benign_pool_file, "r") as f:
        population_samples = f.readlines()
    for sample in population_samples:
        benign_pool.append(util.load_record(sample))
    benign_pool_size = len(benign_pool)
    std_logger.info("Loaded gene pool")

    # Select intial generation
    # generation_indices = random.sample(benign_pool_size, sample_size)
    # for index in generation_indices:
    #     generation.append(benign_pool[int(index)])
    print("Initialization complete")

class DummyFile(object):
    def flush(self): pass
    def write(self, x): pass

@contextlib.contextmanager
def nostdout():
    save_stdout = sys.stdout
    sys.stdout = DummyFile()
    yield
    sys.stdout = save_stdout

class Experiment:

    def __init__(self):
        self.generation = list()
        self.generation.clear()
        self.min_score = 1.0
        self.best_cumulative_extra = list()
        self.best_cumulative_extra.clear()

    def reset_generation(self, seed):
        self.generation = list()

        with nostdout():
            p_labs, p_acc, p_vals = liblinearutil.predict([seed.label], [seed.features], model, '-b 1')
        seed.score = p_vals[0][1]

        self.best_cumulative_extra = [seed] * sample_size

    def evaluate_fitness(self, samples, fitness_rate):
        # Sort samples by maliciousness
        samples.sort(key = lambda sample: sample.score)

        # Replace bottom (1-fitness_rate) with fresh samples
        new_sample_index = int(fitness_rate*sample_size)
        for index in range(new_sample_index, len(samples)):
            if samples[index].score < self.best_cumulative_extra[0].score:
                self.best_cumulative_extra.append(samples[index])
                self.best_cumulative_extra.sort(key = lambda sample: sample.score)
                del self.best_cumulative_extra[-1:]
            else:
                samples[index] = self.best_cumulative_extra[0]
        #     sorted_samples[new_sample_index] = gene_pool[random.randrange(gene_pool_size)]

        return samples

    def mutate_single(self, sample):
        global benign_pool
        new_sample = copy.deepcopy(sample)

        benign_sample = random.choice(benign_pool)

        # num_added_features = random.randrange(len(benign_sample.features.keys()))
        num_added_features = int(random.expovariate(1/math.log(len(benign_sample.features.keys()))))
        for i in range(num_added_features):
            new_feature = random.choice(list(benign_sample.features.keys()))
            new_sample.features[new_feature] = 1
            new_sample.added_feat[new_feature] = 1
            feature_logger.info(new_feature)

        return new_sample

    def mutate_set(self, samples):
        mutation_set = random.sample(range(sample_size), number_unfit)
        for i in mutation_set:
            samples[i] = self.mutate_single(samples[i])

        return samples

    def classify(self, samples):
        global min_score

        # Run model
        generation_input = [sample.features for sample in samples]
        generation_labels = [sample.label for sample in samples]

        with nostdout():
            p_labs, p_acc, p_vals = liblinearutil.predict(generation_labels, generation_input, model, '-b 1')

        for i, val in enumerate(p_vals):
            samples[i].score = val[1]

        samples.sort(key = lambda sample: sample.score)

        generation_best_score = samples[0].score
        if generation_best_score < self.min_score:
            self.min_score = generation_best_score

        std_logger.info("Fitness: " + str(generation_best_score))
        std_logger.debug("----------Sorted samples----------")
        for sample in samples:
            std_logger.debug(sample.stringify())
        std_logger.debug("----------------------------------")

        return samples

    def run_experiment(self, seed, max_gen):
        self.reset_generation(seed)
        current_gen = 0

        for i in range(sample_size):
            self.generation.append(self.mutate_single(seed))
        self.generation = self.classify(self.generation)

        # While evasion performance not good enough or reached max_gen
        while current_gen < max_gen and self.generation[0].score > 0.5:  
            self.generation = self.classify(self.generation)
            self.generation = self.evaluate_fitness(self.generation, mutation_rate)
            self.mutate_set(self.generation)

            num_evaded = sum([member.score < 0.5 for member in self.generation])
            std_logger.info("Generation complete | Evaded: " + str(num_evaded) + " Mutations: " + str(len(self.generation[0].added_feat.keys())))

            # if (current_gen+1) % 5 == 0:
            #     print([member.score for member in self.generation])
                # print("Completed generation", str(current_gen+1), ":", sum([member.score < 0.5 for member in self.generation]))
            current_gen += 1

        if current_gen == max_gen:
            std_logger.warning("Experiment failed - max score: " + str(self.min_score))
            print("Experiment failed - max score: " + str(self.min_score))
        else:
            std_logger.warning("Experiment successful")
            std_logger.info("Num features added: " + str(len(self.generation[0].added_feat.keys())))
            print("Completed generation", str(current_gen+1), ":", sum([member.score < 0.5 for member in self.generation]))
            for feat in self.generation[0].added_feat.keys():
                evasion_logger.info(feat)
        # print([member.score for member in self.generation])

        return sum([member.score < 0.5 for member in self.generation])


def experiment_set(seed):
    (i, seed_string) = seed
    std_logger.info(["----- RUNNING EXPERIMENT ", i, "-----"])
    # print("Running experiment", str(i), "...")
    exp = Experiment()
    final_fitness = exp.run_experiment(util.load_seed(seed_string), 100)
    # print("Experiment " + str(i) + ": " + str(final_fitness))

if __name__ == "__main__":
    init()
    seed_strings = list()
    with open(seed_file, "r") as f:
        seed_strings = f.readlines()

    with Pool(8) as p:
        p.map(experiment_set, enumerate(seed_strings))

    

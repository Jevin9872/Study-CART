# -*- coding: utf-8 -*-
from collections import defaultdict

import torch

CUDA_LAUNCH_BLOCKING="1"
import argparse
import sys
import generate_data
import copy
import random
from config import global_config as cfg
from pn import PolicyNetwork
import time

from conversationModule.epi import run_one_episode
random.seed(1)

the_max = 0
for k,v in cfg.item_dict.items():
    if the_max < max(v['feature_index']):
        the_max = max(v['feature_index'])
print(the_max)
FEATURE_COUNT = the_max + 1

success_at_turn_list_location_random = [0] * 10
success_at_turn_list_item = [0] * 10
success_at_turn_list_location_rate = [0] * 10

def main():

    #一系列可以修改的参数
    parser = argparse.ArgumentParser(description="Run conversational recommendation.")
    parser.add_argument('-mt', type=int, dest='mt', help='MAX_TURN', default = 10)
    parser.add_argument('-playby', type=str, dest='playby', help='playby', default ='policy' )#1
    parser.add_argument('-optim', type=str, dest='optim', help='optimizer', default ='SGD')
    parser.add_argument('-lr', type=float, dest='lr', help='lr', default =0.001)
    parser.add_argument('-decay', type=float, dest='decay', help='decay', default =0)
    parser.add_argument('-TopKTaxo', type=int, dest='TopKTaxo', help='TopKTaxo', default =3)
    parser.add_argument('-gamma', type=float, dest='gamma', help='gamma', default =0)
    parser.add_argument('-trick', type=int, dest='trick', help='trick', default =0)
    parser.add_argument('-startFrom', type=int, dest='startFrom', help='startFrom', default =0)
    parser.add_argument('-endAt', type=int, dest='endAt', help='endAt', default =171) #test 171
    parser.add_argument('-strategy', type=str, dest='strategy', help='strategy', default ='maxsim')
    parser.add_argument('-eval', type=int, dest='eval', help='eval', default =1)#2
    parser.add_argument('-mini', type=int, dest='mini', help='mini', default =1)
    parser.add_argument('-alwaysupdate', type=int, dest='alwaysupdate', help='alwaysupdate', default =1)
    parser.add_argument('-initeval', type=int, dest='initeval', help='initeval', default =0)
    parser.add_argument('-upoptim', type=str, dest='upoptim', help='upoptim', default ='SGD')
    parser.add_argument('-upcount', type=int, dest='upcount', help='upcount', default =4)#3
    parser.add_argument('-upreg', type=float, dest='upreg', help='upreg', default =0.001)#4
    parser.add_argument('-code', type=str, dest='code', help='code', default ='stable')
    parser.add_argument('-purpose', type=str, dest='purpose', help='purpose', default ='train' )#5
    parser.add_argument('-mod', type=str, dest='mod', help='mod', default ='ours')#6
    parser.add_argument('-mask', type=int, dest='mask', help='mask', default =0)#7
    # use for ablation study

    A = parser.parse_args()

    #把参数传入config default={policy, 1, 4, 0.001, train, ours, 0}
    cfg.change_param(playby=A.playby, eval=A.eval, update_count=A.upcount, update_reg=A.upreg,
                     purpose=A.purpose, mod=A.mod, mask=A.mask)

    #代表将torch.Tensor分配到到设备对象
    device = torch.device('cuda')
    #seed()方法改变随机数生成种子，可以在调用其他随机模块函数之前调用此函数。
    #注意：seed()是不能直接访问的，需要导入random模块，然后通过random静态对象调用该方法
    random.seed(1)
    #random.shuffle(cfg.valid_list)
    #random.shuffle(cfg.test_list)
    # 浅拷贝，复制对象，不复制子对象
    the_valid_list_item = copy.copy(cfg.valid_list_item)
    the_valid_list_features = copy.copy(cfg.valid_list_features)
    the_valid_list_location = copy.copy(cfg.valid_list_location)

    the_test_list_item = copy.copy(cfg.test_list_item)
    the_test_list_features = copy.copy(cfg.test_list_features)
    the_test_list_location = copy.copy(cfg.test_list_location)

    the_train_list_item = copy.copy(cfg.train_list_item)
    the_train_list_features = copy.copy(cfg.train_list_features)
    the_train_list_location = copy.copy(cfg.train_list_location)
    #random.shuffle(the_valid_list)
    #random.shuffle(the_test_list)

    gamma = A.gamma #default=0
    transE_model = cfg.transE_model #transEmodel

    #！！这里貌似是保存预训练好到模型
    if A.eval == 1:
        if A.mod == 'ours':
            fp = './data/PN-model-ours/PN-model-ours.txt'
        if A.initeval == 1:

            if A.mod == 'ours':
                fp = './data/PN-model-ours/pretrain-model.pt'
    else:
        # means training
        if A.mod == 'ours':
            fp = './data/PN-model-ours/pretrain-model.pt'

    INPUT_DIM = 0

    if A.mod == 'ours':
        INPUT_DIM = 29 #11+10+8

    PN_model = PolicyNetwork(input_dim=INPUT_DIM, dim1=64, output_dim=12) #conversation里的 newtork，输入为state vector
    start = time.time()

    try:
        PN_model.load_state_dict(torch.load(fp))
        print('Now Load PN pretrain from {}, takes {} seconds.'.format(fp, time.time() - start))
    except:
        print('Cannot load the model!!!!!!!!!\n fp is: {}'.format(fp))
        if cfg.play_by == 'policy':
            sys.exit()

    #确认optimizer（SGD：随机梯度下降；RMS：结合梯度平方的指数移动平均数来调节学习率变化；Adam：结合上述二者优点）
    if A.optim == 'Adam':
        optimizer = torch.optim.Adam(PN_model.parameters(), lr=A.lr, weight_decay=A.decay)
    if A.optim == 'SGD':
        optimizer = torch.optim.SGD(PN_model.parameters(), lr=A.lr, weight_decay=A.decay)
    if A.optim == 'RMS':
        optimizer = torch.optim.RMSprop(PN_model.parameters(), lr=A.lr, weight_decay=A.decay)

    numpy_list = list()
    NUMPY_COUNT = 0

    sample_dict = defaultdict(list)
    conversation_length_list = list()

    combined_num = 0
    total_turn = 0
    #开始训练
    for epi_count in range(A.StartFrom, A.endAt):
        if epi_count % 1 == 0:
            print('-----\nIt has processed {} episodes'.format(epi_count))

        start = time.time()

        current_transE_model = copy.deepcopy(transE_model)
        current_transE_model.to(device)

        param1, param2 = list(), list()
        i = 0
        #获取参数列表
        for name, param in current_transE_model.named_parameters():
            if i == 0 or i==1:
                param1.append(param)
                # param1: head, tail
            else:
                param2.append(param)
                # param2: time, category, cluster, type
            i += 1

        '''change to transE embedding'''
        optimizer1_transE, optimizer2_transE = None, None
        if A.purpose != 'fmdata':
            optimizer1_transE = torch.optim.Adagrad(param1, lr=0.01, weight_decay=A.decay)
            if A.upoptim == 'Ada':
                optimizer2_transE = torch.optim.Adagrad(param2, lr=0.01, weight_decay=A.decay)
            if A.upoptim == 'SGD':
                optimizer2_transE = torch.optim.SGD(param2, lr=0.001, weight_decay=A.decay)

        #判断是否是pretrain
        if A.purpose != 'pretrain':
            items = the_valid_list_item[epi_count]  #0 18 10 3
            features = the_valid_list_features[epi_count] #3,21,2,1    21,12,2,1   22,7,2,1
            location = the_valid_list_location[epi_count]
            item_list = items.strip().split(' ')
            u = item_list[0]
            item = item_list[-1]
            if A.eval == 1:
                u, item, l = the_test_list_item[epi_count]
                items = the_test_list_item[epi_count]  # 0 18 10 3
                features = the_test_list_features[epi_count]  # 3,21,2,1    21,12,2,1   22,7,2,1
                location = the_test_list_location[epi_count]
                item_list = items.strip().split(' ')
                u = item_list[0]
                item = item_list[-1]

            user_id = int(u)
            item_id = int(item)
            location_id = int(location)
        else:
            user_id = 0
            item_id = epi_count

        if A.purpose == 'pretrain':
            items = the_train_list_item[epi_count]  #0 18 10 3
            features = the_train_list_features[epi_count] #3,21,2,1    21,12,2,1   22,7,2,1
            location = the_train_list_location[epi_count]
            item_list = items.strip().split(' ')
            u = item_list[0]
            item = item_list[-1]
            user_id = int(u)
            item_id = int(item)
            location_id = int(location)
        print ("----target item: ", item_id)
        big_feature_list = list()

        '''update L2.json'''
        for k, v in cfg.taxo_dict.items():
            #print (k,v)
            if len(set(v).intersection(set(cfg.item_dict[str(item_id)]['L2_Category_name']))) > 0:
                #print(user_id, item_id) #433,122
                #print (k)
                big_feature_list.append(k)

        #将操作保存,记录类似conversion步骤
        write_fp = './data/interaction-log/{}/v4-code-{}-s-{}-e-{}-lr-{}-gamma-{}-playby-{}-stra-{}-topK-{}-trick-{}-eval-{}-init-{}-mini-{}-always-{}-upcount-{}-upreg-{}-m-{}.txt'.format(
            A.mod.lower(), A.code, A.startFrom, A.endAt, A.lr, A.gamma, A.playby, A.strategy, A.TopKTaxo, A.trick,
            A.eval, A.initeval,
            A.mini, A.alwaysupdate, A.upcount, A.upreg, A.mask)

        '''care the sequence of facet pool items'''
        if cfg.item_dict[str(item_id)]['POI_Type'] is not None:
            choose_pool = ['clusters', 'POI_Type'] + big_feature_list

        choose_pool_original = choose_pool

        if A.purpose not in ['pretrain', 'fmdata']:
            choose_pool = [random.choice(choose_pool)]

        #！！训练！！
        for c in choose_pool:
            start_facet = c
            with open(write_fp, 'a') as f:
                f.write('Starting new\nuser ID: {}, item ID: {} episode count: {}\n'.format(user_id, item_id, epi_count))

            if A.purpose != 'pretrain':
                log_prob_list, rewards, success, turn_count, known_feature_category = run_one_episode(current_transE_model, user_id, item_id, A.mt, False, write_fp,
                                                         A.strategy, A.TopKTaxo,
                                                         PN_model, gamma, A.trick, A.mini,
                                                         optimizer1_transE, optimizer2_transE, A.alwaysupdate, start_facet, A.mask, sample_dict, choose_pool_original,features, items)

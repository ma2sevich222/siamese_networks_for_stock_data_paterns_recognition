import numpy as np
import pandas as pd
import plotly.express as px
import torch
import os
import optuna
from datetime import date
import random
from sklearn.preprocessing import StandardScaler
from torch.nn import functional as F
from torch.utils.data import TensorDataset, DataLoader
from models.torch_models import shotSiameseNetwork
from utilits.project_functions import (
    get_train_data,
    get_triplet_random,
    train_triplet_net,
    find_best_dist_stbl,
    get_signals,
    uptune_get_stat_after_forward,
)

today = date.today()
date_xprmnt = today.strftime("%d_%m_%Y")
source = "source_root"
out_root = "outputs"
source_file_name = "GC_2020_2022_60min.csv"
out_data_root = f"{source_file_name[:-4]}_uptune_{date_xprmnt}"
os.mkdir(f"{out_root}/{out_data_root}")
n_trials = 300
intermedia = pd.DataFrame()
intermedia.to_excel(
    f"{out_root}/{out_data_root}/intermedia_{source_file_name[:-4]}.xlsx"
)


def objective(trial):

    torch.manual_seed(2020)
    torch.cuda.manual_seed(2020)
    np.random.seed(2020)
    random.seed(2020)
    torch.backends.cudnn.deterministic = True
    """""" """""" """""" """""" """"" Общие настройки """ """""" """""" """""" """"""

    start_forward_time = "2021-03-25 00:00:00"
    df = pd.read_csv(f"{source}/{source_file_name}")
    forward_index = df[df["Datetime"] == start_forward_time].index[0]
    step = 0.1
    get_trade_info = True
    distance_function = lambda x, y: 1.0 - F.cosine_similarity(x, y)
    """""" """""" """""" """""" """"" Параметры для оптимизации   """ """ """ """ """ """ """ """ """ ""
    extr_window = trial.suggest_int("extr_window", 60, 120, step=60)
    pattern_size = trial.suggest_int("pattern_size", 10, 40, step=10)
    overlap = trial.suggest_int("overlap", 0, 20, step=10)
    epochs = trial.suggest_int("epochs", 2, 8, step=2)
    batch_size = trial.suggest_int("batch_size", 50, 150, step=50)
    embedding_dim = trial.suggest_int("batch_size", 50, 200, step=50)
    lr = trial.suggest_loguniform("lr", 1e-6, 1e-3)
    margin = trial.suggest_discrete_uniform("margin", 1.0, 10.0, 1.0)
    profit_value = trial.suggest_discrete_uniform("profit_value", 0, 0.02, 0.01)
    train_window = trial.suggest_int("train_window", 2400, 2500, step=100)
    select_dist_window = trial.suggest_int("select_dist_window", 1500, 2500, step=1000)
    forward_window = trial.suggest_int(
        "forward_window", 1, 3, step=1,
    )  # в частях общего df
    df_for_split = df[forward_index - train_window - select_dist_window :]
    df_for_split = df_for_split.reset_index(drop=True)
    signals = []
    cut_lenth = (
        len(df_for_split) - train_window - select_dist_window
    ) // forward_window
    for n in range(forward_window):

        train_df = df_for_split[:train_window]
        test_df = df_for_split[train_window : sum([train_window, select_dist_window])]
        forward_df = df_for_split[
            sum([train_window, select_dist_window]) : sum(
                [
                    train_window,
                    select_dist_window,
                    (len(df_for_split) - train_window - select_dist_window)
                    // forward_window,
                ]
            )
        ]
        df_for_split = df_for_split[cut_lenth:]
        df_for_split = df_for_split.reset_index(drop=True)
        train_df = train_df.reset_index(drop=True)
        test_df = test_df.reset_index(drop=True)
        forward_df = forward_df.reset_index(drop=True)
        train_dates = pd.DataFrame({"Datetime": train_df["Datetime"].values})
        test_dates = pd.DataFrame({"Datetime": test_df["Datetime"].values})
        forward_dates = pd.DataFrame({"Datetime": forward_df.Datetime.values})
        del (
            train_df["Datetime"],
            test_df["Datetime"],
            forward_df["Datetime"],
        )

        train_x, n_samples_to_train = get_train_data(
            train_df, profit_value, extr_window, pattern_size, overlap, train_dates,
        )  # получаем данные для создания триплетов

        n_classes = len(train_x)
        train_triplets = get_triplet_random(n_samples_to_train, n_classes, train_x)
        print(
            " Размер данных для обучения:", np.array(train_triplets).shape,
        )

        tensor_A = torch.Tensor(train_triplets[0])  # transform to torch tensor
        tensor_P = torch.Tensor(train_triplets[1])
        tensor_N = torch.Tensor(train_triplets[2])

        my_dataset = TensorDataset(tensor_A, tensor_P, tensor_N)  # create your datset
        my_dataloader = DataLoader(my_dataset, batch_size=batch_size)

        """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" ""
        """""" """""" """""" """""" """"" Train net  """ """""" """""" """""" """"""

        net = shotSiameseNetwork(embedding_dim=embedding_dim).cuda()
        torch.cuda.empty_cache()
        train_triplet_net(lr, epochs, my_dataloader, net, distance_function, margin)

        """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" ""
        """""" """""" """""" """""" """"" Test data prepare  """ """""" """""" """""" """"""
        scaler = StandardScaler()
        eval_array = test_df.to_numpy()
        eval_samples = [
            eval_array[i - pattern_size : i]
            for i in range(len(eval_array))
            if i - pattern_size >= 0
        ]
        eval_normlzd = [scaler.fit_transform(i) for i in eval_samples]
        eval_normlzd = np.array(eval_normlzd).reshape(
            -1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
        )

        """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" ""
        """""" """""" """""" """""" """"" Test model  """ """""" """""" """""" """"""

        date = []
        open = []
        high = []
        low = []
        close = []
        volume = []
        buy_pred = []
        train_data_shape = []

        net.eval()
        with torch.no_grad():
            for indexI, eval_arr in enumerate(eval_normlzd):
                anchor = train_x[0][0].reshape(
                    1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
                )
                eval_arr_r = eval_arr.reshape(
                    1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
                )
                anchor = torch.Tensor(anchor)
                eval_arr_r = torch.Tensor(eval_arr_r)
                output1, output2, output3 = net(
                    anchor.cuda().permute(0, 3, 1, 2),
                    eval_arr_r.cuda().permute(0, 3, 1, 2),
                    eval_arr_r.cuda().permute(0, 3, 1, 2),
                )
                net_pred = distance_function(output1, output3)
                buy_pred.append(float(net_pred.to("cpu").numpy()))

                date.append(test_dates.Datetime[indexI + (pattern_size - 1)])
                open.append(float(eval_samples[indexI][-1, [0]]))
                high.append(float(eval_samples[indexI][-1, [1]]))
                low.append(float(eval_samples[indexI][-1, [2]]))
                close.append(float(eval_samples[indexI][-1, [3]]))
                volume.append(float(eval_samples[indexI][-1, [4]]))
                train_data_shape.append(float(train_df.shape[0]))

        test_result = pd.DataFrame(
            {
                "Datetime": date,
                "Open": open,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
                "Distance": buy_pred,
                "Train_shape": train_data_shape,
            }
        )

        buy_before, sell_after = find_best_dist_stbl(test_result, step)

        print(f"BUY BEFORE = {buy_before}")
        print(f"SELL AFTER = {sell_after}")

        """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" ""
        """""" """""" """""" """""" """"" Forward data prepare  """ """""" """""" """""" """"""
        scaler = StandardScaler()
        eval_array = forward_df.to_numpy()
        eval_samples = [
            eval_array[i - pattern_size : i]
            for i in range(len(eval_array))
            if i - pattern_size >= 0
        ]
        eval_normlzd = [scaler.fit_transform(i) for i in eval_samples]
        eval_normlzd = np.array(eval_normlzd).reshape(
            -1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
        )

        """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" """""" ""
        """""" """""" """""" """""" """"" Forward model  """ """""" """""" """""" """"""

        date = []
        open = []
        high = []
        low = []
        close = []
        volume = []
        buy_pred = []
        train_data_shape = []

        net.eval()
        with torch.no_grad():
            for indexI, eval_arr in enumerate(eval_normlzd):
                anchor = train_x[0][0].reshape(
                    1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
                )
                eval_arr_r = eval_arr.reshape(
                    1, eval_samples[0].shape[0], eval_samples[0][0].shape[0], 1,
                )
                anchor = torch.Tensor(anchor)
                eval_arr_r = torch.Tensor(eval_arr_r)
                output1, output2, output3 = net(
                    anchor.cuda().permute(0, 3, 1, 2),
                    eval_arr_r.cuda().permute(0, 3, 1, 2),
                    eval_arr_r.cuda().permute(0, 3, 1, 2),
                )
                net_pred = distance_function(output1, output3)
                buy_pred.append(float(net_pred.to("cpu").numpy()))

                date.append(forward_dates.Datetime[indexI + (pattern_size - 1)])
                open.append(float(eval_samples[indexI][-1, [0]]))
                high.append(float(eval_samples[indexI][-1, [1]]))
                low.append(float(eval_samples[indexI][-1, [2]]))
                close.append(float(eval_samples[indexI][-1, [3]]))
                volume.append(float(eval_samples[indexI][-1, [4]]))
                train_data_shape.append(float(train_df.shape[0]))

        forward_result = pd.DataFrame(
            {
                "Datetime": date,
                "Open": open,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
                "Distance": buy_pred,
                "Train_shape": train_data_shape,
            }
        )

        signal = get_signals(forward_result, buy_before, sell_after)
        signals.append(signal)

    signals_combained = pd.concat(signals, ignore_index=True, sort=False)

    df_stata = uptune_get_stat_after_forward(
        signals_combained,
        pattern_size,
        extr_window,
        overlap,
        train_window,
        select_dist_window,
        forward_window,
        profit_value,
        source_file_name,
        out_root,
        out_data_root,
        trial.number,
        get_trade_info=get_trade_info,
    )

    parameters = trial.params
    net_profit = df_stata["Net Profit [$]"].values[0]
    Sharpe_Ratio = df_stata["Sharpe Ratio"].values[0]
    parameters.update({"trial": trial.number})
    parameters.update({"values_0": net_profit})
    parameters.update({"values_1": Sharpe_Ratio})
    inter = pd.read_excel(
        f"{out_root}/{out_data_root}/intermedia_{source_file_name[:-4]}.xlsx"
    )
    inter = inter.append(parameters, ignore_index=True)
    inter.to_excel(
        f"{out_root}/{out_data_root}/intermedia_{source_file_name[:-4]}.xlsx",
        index=False,
    )

    torch.save(net.state_dict(), f"{out_root}/{out_data_root}/weights.pt")

    return net_profit, Sharpe_Ratio


study = optuna.create_study(directions=["maximize", "maximize"])
study.optimize(objective, n_trials=n_trials)


tune_results = study.trials_dataframe()
"""tune_results = tune_results.rename(
    columns={"values_0": "Net_profit [$]", "values_1": "Sharpe_Ratio"}
)
print(tune_results)"""

df_plot = tune_results[
    [
        "values_0",
        "values_1",
        "params_pattern_size",
        "params_extr_window",
        "params_overlap",
        "params_batch_size",
        "params_epochs",
        "params_lr",
        "params_margin",
        "params_train_window",
        "params_select_dist_window",
        "params_forward_window",
    ]
]

fig = px.parallel_coordinates(
    df_plot,
    color="values_0",
    labels={
        "values_0": "Net Profit ($)",
        "values_1": "Sharpe_Ratio",
        "params_pattern_size": "pattern_size (bars)",
        "params_extr_window": "extr_window (bars)",
        "params_overlap": "overlap (bars)",
        "params_batch_size": "batch_size",
        "params_epochs": "epochs",
        "params_lr": "learning_rate",
        "params_margin": "margin",
        "params_train_window": "train_window (bars)",
        "params_select_dist_window": "select_dist_window (bars)",
        "params_forward_window": "forward_window (parts)",
    },
    range_color=[df_plot["values_0"].min(), df_plot["values_0"].max()],
    color_continuous_scale=px.colors.sequential.Viridis,
    title=f"hyp_parameters_select_{source_file_name[:-4]}",
)

fig.write_html(f"{out_root}/{out_data_root}/hyp_par_sel_{source_file_name[:-4]}.htm")
fig.show()
tune_results.to_excel(
    f"{out_root}/{out_data_root}/hyp_par_sel_{source_file_name[:-4]}.xlsx"
)

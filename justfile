# Task runner for the Nix development shell (see docs/plans/unity-simulator.md).
# Nix 開発シェル用のタスクランナー（docs/plans/unity-simulator.md 参照）。
#
# Firmware builds stay with the sf CLI after `source setup_env.sh`; this file
# only holds tasks that run inside `nix develop`.
# ファームウェアのビルドは従来どおり `source setup_env.sh` 後の sf CLI が担う。
# このファイルには `nix develop` の中で動くタスクだけを置く。

# Show the available recipes
# 利用できるレシピの一覧を表示する
default:
    @just --list

# Format the files covered by treefmt (flake.nix and files added with it)
# treefmt の対象ファイル（flake.nix と一緒に新設したファイル）を整形する
fmt:
    nix fmt

# Run every flake check, including the formatting check
# flake の検査を全て実行する（整形の検査を含む）
check:
    nix flake check

# Verify that this justfile is formatted as `just --fmt` would write it
# この justfile が `just --fmt` の出力どおりの書式かを確かめる
lint-just:
    just --fmt --check --unstable

# Rebuild the stage 1(a) WebAssembly spike and fly it for N simulated seconds
# 段階 1(a) の WebAssembly 技術検証を作り直し、N 秒ぶん飛ばす
unity-native-spike seconds="30":
    bash simulator/unity/native/spike/build_spike.sh {{ seconds }}

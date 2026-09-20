{
  # Development shell for the native (C/C++/WebAssembly) parts of the Unity
  # simulator. See docs/plans/unity-simulator.md.
  # Unity 版シミュレータのネイティブ部分（C/C++/WebAssembly）のための開発シェル。
  # 詳細は docs/plans/unity-simulator.md を参照。
  #
  # Why not put ESP-IDF and the sf CLI here: those keep their existing entry
  # point, setup_env.sh, which installs a dedicated Python and ESP-IDF
  # (PROJECT_PLAN.md §12). This flake only adds build tools, so the two
  # environments do not overlap and neither has to change for the other.
  # Why not（ESP-IDF と sf CLI をここに入れない理由）: それらは従来どおり
  # setup_env.sh が有効化する（専用 Python と専用 ESP-IDF、PROJECT_PLAN.md §12）。
  # この flake はビルド道具だけを足すので、両者は重ならず、互いに変更を要さない。
  description = "Build tools for the native parts of the StampFly Unity simulator";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      treefmt-nix,
    }:
    let
      # The platforms this repository is developed on.
      # Why not flake-utils: listing two systems by hand keeps the input set
      # small, and this flake has no other use for the library.
      # Why not（flake-utils を使わない理由）: 2 つ列挙するだけで済み、入力を
      # 増やさずに書ける。この flake では他にライブラリの用途が無い。
      systems = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # Formatter definition shared by `nix fmt` and `nix flake check`.
      # `nix fmt` と `nix flake check` が共有する整形定義。
      treefmtFor =
        pkgs:
        treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";

          # Scope: only the files introduced with the Nix development shell.
          # Why not format the whole repository: the existing C++, Python, and
          # Markdown files follow conventions of their own (AGENTS.md), and a
          # repository-wide reformat would rewrite thousands of lines that have
          # nothing to do with this change.
          # 対象は Nix 開発シェルと一緒に新設したファイルだけに限る。
          # Why not（リポジトリ全体を整形しない理由）: 既存の C++・Python・Markdown
          # はそれぞれ独自の規約（AGENTS.md）に従っており、全体整形はこの変更と
          # 無関係な数千行を書き換えてしまう。
          programs.nixfmt.enable = true;
          programs.nixfmt.package = pkgs.nixfmt-rfc-style;
          settings.formatter.nixfmt.includes = nixpkgs.lib.mkForce [ "flake.nix" ];

          # shfmt stays off until this change set adds a shell script of its
          # own. Why not enable it with an empty file list: treefmt refuses a
          # formatter that matches nothing, and enabling it repository-wide
          # would reformat install.sh and setup_env.sh.
          # Why not（shfmt を今は有効にしない理由）: この変更で新設するシェル
          # スクリプトがまだ無い。対象を空にして有効化すると treefmt が
          # 「対象の無い整形器」を拒否し、リポジトリ全体を対象にすると
          # install.sh・setup_env.sh を書き換えてしまう。
          programs.shfmt.enable = false;
        };
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShellNoCC {
          # Why mkShellNoCC (no compiler): on macOS the native plugin is built
          # with Xcode's clang so that it links against the same SDK Unity uses;
          # a Nix-provided compiler would pull in a second toolchain and a
          # second libc++. CMake finds the system compiler at configure time.
          # Why not（コンパイラを入れない理由）: macOS ではネイティブプラグインを
          # Xcode の clang でビルドし、Unity と同じ SDK にリンクさせる。Nix 側の
          # コンパイラを入れると 2 つ目のツールチェインと 2 つ目の libc++ が
          # 混ざる。CMake は configure 時に system のコンパイラを見つける。
          packages = with pkgs; [
            cmake
            ninja
            just
            lefthook
            shellcheck
            gitleaks
            emscripten
            nodejs
          ];

          shellHook = ''
            echo "StampFly native build shell / ネイティブビルド用シェル"
            echo "  ESP-IDF and sf CLI: source setup_env.sh (separate environment)"
            echo "  ESP-IDF と sf CLI: source setup_env.sh（別の環境）"
          '';
        };
      });

      formatter = forAllSystems (pkgs: (treefmtFor pkgs).config.build.wrapper);

      checks = forAllSystems (pkgs: {
        formatting = (treefmtFor pkgs).config.build.check self;
      });
    };
}

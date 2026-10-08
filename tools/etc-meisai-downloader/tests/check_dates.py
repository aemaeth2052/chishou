"""検索する日の選び方の確認。画面（App）を作って、ボタンやカレンダーと同じ処理を呼ぶ。
GitHub Actions の Windows 上と、手元で実行する（使い方: python tests\\check_dates.py）。

- 起動したときは昨日1日が選ばれていて、「昨日」ボタンが青い
- ◀ ▶ で前後の日へ動く。今日より先・62日より前には動かない
- 「期間で指定する」で開始日〜終了日になり、日数が出る。外すと1日に戻る
- 期間で、終了日より後の開始日をカレンダーで選ぶと、終了日もその日にそろう
- 「昨日」ボタンで、いつでも昨日1日に戻る
"""

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402


def fmt(d):
    return d.strftime("%Y/%m/%d")


def main():
    app.browser_setup.ensure_chromium_async = lambda **_kw: None  # ブラウザの確認（と最初の案内）はしない
    a = app.App()
    a.update()
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    label = lambda: a.period_label.cget("text")  # noqa: E731

    assert (a.var_from.get(), a.var_to.get(), a.var_span.get()) == (fmt(yesterday), fmt(yesterday), "day")
    assert a.btn_yesterday.cget("style") == "Accent.TButton"
    assert "昨日の分" in label(), label()
    print("起動時は昨日: ok")

    a._step_day(-1)
    assert a.var_to.get() == fmt(yesterday - datetime.timedelta(days=1)), "終了日が開始日についてきません"
    assert a.btn_yesterday.cget("style") == "TButton" and "一昨日の分" in label(), label()
    for _ in range(3):
        a._step_day(1)
    assert a.var_from.get() == fmt(today) and a.btn_next_day.instate(["disabled"]), a.var_from.get()
    for _ in range(app.MAX_DAYS + 5):
        a._step_day(-1)
    assert a.var_from.get() == fmt(today - datetime.timedelta(days=app.MAX_DAYS)), a.var_from.get()
    assert a.btn_prev_day.instate(["disabled"])
    print("◀ ▶: ok")

    a.var_use_range.set(True)
    a._set_span("range")
    a.var_from.set(fmt(today - datetime.timedelta(days=6)))
    a.var_to.set(fmt(yesterday))
    assert "6日間" in label(), label()
    a._open_calendar_popup(a.btn_yesterday, a.var_from)
    a.update()
    popup = [w for w in a.winfo_children() if w.winfo_class() == "Toplevel"][-1]
    popup._pick(today)
    assert a.var_from.get() == a.var_to.get() == fmt(today), (a.var_from.get(), a.var_to.get())
    a.var_use_range.set(False)
    a._set_span("day")
    assert a.var_to.get() == a.var_from.get()
    print("期間で指定する: ok")

    a.var_from.set("2026/13/40")
    assert "形が違います" in label(), label()
    a.set_yesterday()
    assert (a.var_from.get(), a.var_to.get(), a.var_span.get(), a.var_use_range.get()) == \
        (fmt(yesterday), fmt(yesterday), "day", False)
    print("昨日に戻る: ok")
    a.destroy()
    print("ok")


if __name__ == "__main__":
    main()

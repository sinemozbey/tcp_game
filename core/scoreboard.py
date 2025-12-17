# core/scoreboard.py

from dataclasses import dataclass


@dataclass
class Scoreboard:
    """
    Scoreboard for TCP Game.

    Scoring philosophy (hocaya anlatım):
    - Hata yapan taraf 1 puan kaybeder
    - Hatası yakalanan tarafın rakibi 1 puan kazanır
    - Hatası fark edilmeyen taraf avantaj sağlar (+1)
    - Timeout bir hata türü olarak değerlendirilir
    """

    my_score: int = 0
    opponent_score: int = 0  # sadece gösterim / debug amaçlı

    # --------------------------------------------------
    # ERROR SCENARIOS
    # --------------------------------------------------

    def opponent_made_error(self):
        """
        Rakip mantıksal olarak hatalı paket gönderdi
        ve biz bunu doğru şekilde tespit ettik.
        """
        self.my_score += 1

    def my_error_undetected(self):
        """
        Biz hatalı paket gönderdik ama rakip bunu fark edemedi.
        (Dokümana göre: gönderen taraf +1 kazanır)
        """
        self.my_score += 1

    def i_made_error(self):
        """
        Biz mantıksal hata yaptık ve rakip bunu tespit etti.
        """
        self.my_score -= 1

    # --------------------------------------------------
    # TIMEOUT SCENARIOS
    # --------------------------------------------------

    def opponent_timeout(self):
        """
        Rakip, cevap vermesi gereken durumda süreyi aştı.
        """
        self.my_score += 1

    def my_timeout(self):
        """
        Biz, cevap vermemiz gereken durumda süreyi aştık.
        """
        self.my_score -= 1

    # --------------------------------------------------
    # DISPLAY
    # --------------------------------------------------

    def snapshot(self) -> str:
        return f"Score → ME: {self.my_score}"

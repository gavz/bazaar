from tempfile import NamedTemporaryFile

from androguard.core.androconf import is_android
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from elasticsearch import Elasticsearch
from rest_framework.reverse import reverse_lazy

from bazaar.core.tasks import analyze
from bazaar.core.utils import get_sha256_of_file
from bazaar.front.forms import SearchForm, BasicUploadForm
from bazaar.front.utils import transform_results, get_similarity_matrix


@method_decorator(csrf_exempt, name='dispatch')
class HomeView(View):

    def get(self, request, *args, **kwargs):
        # Gets the latest complete report as an example
        es = Elasticsearch([settings.ELASTICSEARCH_HOST])
        q = {
            "size": 1,
            "sort": {"analysis_date": "desc"},
            "query": {
                "match_all": {}
            },
            "_source": ["handle", "apk_hash", "quark"]
        }
        report_example = es.search(index=settings.ELASTICSEARCH_APK_INDEX, body=q)
        tmp = transform_results(report_example)
        if tmp:
            report_example = tmp[0]
        else:
            report_example = tmp

        q = None
        matrix = None
        results = None
        list_results = False
        aggregations = []

        f = SearchForm(request.GET)
        form_to_show = f
        if not request.GET:
            form_to_show = SearchForm()
        if f.is_valid():
            results, aggregations = f.do_search()
            list_results = True
            q = f.cleaned_data['q']
            matrix = get_similarity_matrix(results)

        return render(request,
                      'front/index.html',
                      {
                          'form': form_to_show,
                          'results': results,
                          'aggregations': aggregations,
                          'upload_form': BasicUploadForm(),
                          'list_results': list_results,
                          'report_example': report_example,
                          'q': q, 'matrix': matrix,
                          'max_size': settings.MAX_APK_UPLOAD_SIZE
                      })


class ReportView(View):

    def get(self, request, *args, **kwargs):
        if 'sha256' not in kwargs:
            return redirect(reverse_lazy('front:home'))

        sha = kwargs['sha256']
        cache_key = f'html_report_{sha}'

        # First, check if the report is already in cache
        cached_report = cache.get(cache_key)
        if cached_report:
            return cached_report

        # Not cached so, let's compute the report
        es = Elasticsearch([settings.ELASTICSEARCH_HOST])
        try:
            result = es.get(index=settings.ELASTICSEARCH_APK_INDEX, id=sha)['_source']
            if 'analysis_date' not in result:
                messages.info(request, 'The analysis is still running, refresh this page in few minutes.')
                return render(request, 'front/report.html', {'result': result})
            else:
                # The analysis is done so put the report into the cache
                html_report = render(request, 'front/report.html', {'result': result})
                cache.set(cache_key, html_report)
                return html_report
        except Exception:
            return redirect(reverse_lazy('front:home'))


def basic_upload_view(request):
    if request.method == 'POST':
        form = BasicUploadForm(request.POST, request.FILES)
        if form.is_valid():
            apk = request.FILES['apk']
            if apk.size > settings.MAX_APK_UPLOAD_SIZE:
                messages.warning(request, 'Submitted file is too large.')
                return redirect(reverse_lazy('front:home'))

            with NamedTemporaryFile() as tmp:
                for chunk in apk.chunks():
                    tmp.write(chunk)
                tmp.seek(0)

                if is_android(tmp.name) != 'APK':
                    messages.warning(request, 'Submitted file is not a valid APK.')
                    return redirect(reverse_lazy('front:home'))

                sha256 = get_sha256_of_file(tmp)
                if default_storage.exists(sha256):
                    # analyze(sha256, force=True)
                    return redirect(reverse_lazy('front:report', [sha256]))
                else:
                    default_storage.save(sha256, tmp)
                    analyze(sha256)
                    return redirect(reverse_lazy('front:report', [sha256]))

    return redirect(reverse_lazy('front:home'))
